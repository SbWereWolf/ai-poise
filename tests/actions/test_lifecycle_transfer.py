"""Real same-schema packages carry initial and restarted native actions."""
from copy import deepcopy
import json
import shutil

import pytest

from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.modules.actions.domain import PlanSpec
from poise.modules.foundation.errors import PoiseError
from transfer.helpers import destination, enabled, export, handoff_args, pick, restore
from .helpers import verify
from .lifecycle_helpers import command_plan, metadata, prepare, restart_current, runtime


def action_rows(client, task_id):
    # Imported database-global sequence IDs may be reassigned by their owner;
    # the physical identity, versions, full payloads and event order may not.
    with client.store.transaction() as database:
        runs = [tuple(row) for row in database.execute(
            "SELECT stage,iteration,version,data FROM action_runs WHERE task_id=? ORDER BY stage,iteration",
            (task_id,),
        )]
        events = [tuple(row) for row in database.execute(
            "SELECT stage,iteration,version,at,data FROM action_events WHERE task_id=? ORDER BY seq",
            (task_id,),
        )]
    return {"runs": runs, "events": events}


@pytest.mark.parametrize("restarted", [False, True], ids=["initial", "restarted"])
def test_public_package_preserves_action_history_current_context_and_replay(project, tmp_path, restarted):
    enabled(project)
    client, context = runtime(project)
    marker = tmp_path / "package-effects.txt"
    current = command_plan(marker, "current")
    prior = command_plan(marker, "prior")
    verify(client, prepare(context, prior if restarted else current))
    old_rows = action_rows(client, context["task"])
    if restarted:
        client, context = restart_current(client, context, suffix="package")
        if context["action"] is not None:
            pytest.fail("LIFECYCLE_TRANSFER_SELECTED_PRIOR_ACTION")
        completed = verify(client, prepare(context, current))
        assert completed["action"]["plan"] == current
        assert completed["action"]["status"] == "complete"
    history = deepcopy(metadata(client, context["task"]).get("restart_history", []))
    before = action_rows(client, context["task"])
    expected_keys = [("apply", 1)]
    if restarted:
        # Native audit is the independently observed input. The expected key
        # uses this test's literal protocol; no subject codec constructs it.
        boundary = history[-1]["from_version"]
        expected_keys.append((f'[{boundary},"apply"]', -1))
    assert {row[:2] for row in before["runs"]} == set(expected_keys)
    assert {row[:2] for row in before["events"]} == set(expected_keys)
    assert all(row in before["runs"] for row in old_rows["runs"])
    assert before["events"][:len(old_rows["events"])] == old_rows["events"]
    expected_plans = [prior, current] if restarted else [current]
    actual_plans = [json.loads(row[3])["plan"] for row in before["runs"]]
    assert len(actual_plans) == len(expected_plans)
    assert all(plan in actual_plans for plan in expected_plans)
    assert all(json.loads(row[3])["status"] == "complete" for row in before["runs"])
    assert marker.read_text() == ("prior\ncurrent\n" if restarted else "current\n")
    saved = export(WorkTools(client), handoff=handoff_args(None))
    assert saved["status"] == "exported"
    transfer_project = {**project, "cfg": json.loads(project["config_path"].read_text())}
    target = destination(transfer_project, tmp_path / "package-destination")
    receiver = WorkPoise(target["config_path"], "PACKAGE-RECEIVER")
    tools = WorkTools(receiver)
    imported = restore(tools, saved["package_path"], saved["package_digest"])
    assert imported["status"] == "imported"
    assert imported["task_ids"] == [context["task"]]
    assert action_rows(receiver, context["task"]) == before
    assert metadata(receiver, context["task"]).get("restart_history", []) == history
    shutil.rmtree(project["root"])
    resumed = pick(tools, context["task"])
    assert resumed["status"] == "verified"
    assert resumed["stage"] == "apply" and resumed["iteration"] == 1
    assert resumed["worktree"] != context["worktree"]
    assert resumed["action"]["plan"] == current
    assert resumed["action"]["status"] == "complete"
    after_pick = action_rows(receiver, context["task"])
    assert after_pick == before
    effect = marker.read_bytes()
    replay = verify(receiver, None)
    assert replay["replayed"] is True
    assert replay["action"]["plan"] == current
    assert replay["action"]["status"] == "complete"
    assert action_rows(receiver, context["task"]) == after_pick
    assert marker.read_bytes() == effect


def test_package_refuses_retained_prepared_action_when_current_is_complete(project, tmp_path):
    enabled(project)
    client, context = runtime(project)
    marker = tmp_path / "refused-package-effects.txt"
    prior = PlanSpec.parse(command_plan(marker, "never-applied"), 1)
    client.plan_actions.commands.obtain(context["task"], client.session, "apply", 1, prior)
    client, context = restart_current(client, context, suffix="retained-incomplete")
    if context["action"] is not None:
        pytest.fail("LIFECYCLE_TRANSFER_SELECTED_RETAINED_PREPARED")
    current = command_plan(marker, "current")
    assert verify(client, prepare(context, current))["action"]["status"] == "complete"
    before = action_rows(client, context["task"])
    assert {json.loads(row[3])["status"] for row in before["runs"]} == {"prepared", "complete"}
    assert marker.read_text() == "current\n"
    with pytest.raises(PoiseError, match="^Resolve the external action before transfer$"):
        export(WorkTools(client), handoff=handoff_args(None))
    # Combined handoff legitimately precedes export's all-action validation.
    assert client.current_task() is None
    assert action_rows(client, context["task"]) == before
    assert marker.read_text() == "current\n"
    assert list(project["root"].rglob("work.zip")) == []
