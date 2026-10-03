"""Authorized legacy restart must distinguish historical and revised Requirements context."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
import os
import sys

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import bind_task_requirements
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import git, restart
from tasks.test_newborn_lifecycle import complete_patch, create, edit, task_action, tools
from tasks.test_planning_flexibility import full_draft


INCIDENTS = (
    ("ERP-EXCEL-IN04", "Excel export obeys its revised Requirements context."),
    ("ERP-HARNESS-P001", "ERP harness obeys its revised Requirements context."),
    ("AIP-MCP-CLEANUP-GUARD-001", "MCP cleanup guard obeys its revised Requirements context."),
)
HISTORICAL_REFUSAL = "Restarted Task Requirements context differs from persisted state"


def _assert_source_identity() -> None:
    import poise

    root = Path(__file__).resolve().parents[2]
    assert Path(poise.__file__).resolve().is_relative_to(root / "src"), (
        poise.__file__, root
    )
    assert Path(os.environ["PYTHONPATH"]).resolve() == root / "src"


def _ready(client, task_id: str, revision: int, request_id: str):
    return task_action(
        client,
        action="ready",
        task_id=task_id,
        expected_revision=revision,
        request_id=request_id,
    )


def _new_revised_context(client, project, requirement: str) -> dict:
    contract = deepcopy(project["task"])
    contract["requirements"] = [requirement]
    bind_task_requirements(contract, client.runtime.requirements_commands.store.registry())
    return {
        key: deepcopy(contract[key])
        for key in ("requirements", "requirements_snapshot", "requirements_agreement")
    }


def _create_restarted(project, task_id: str, requirement: str):
    _assert_source_identity()
    client = tools(project, f"owner-{task_id.lower()}")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, f"fill-{task_id}")
    first = _ready(client, task_id, filled["revision"], f"first-ready-{task_id}")
    original = deepcopy(client.runtime.task_queries.record(task_id))
    restarted = restart(
        client,
        task_id,
        first["revision"],
        request_id=f"restart-{task_id}",
        authorization="User explicitly authorized this unfinished legacy Task restart.",
    )
    revised = _new_revised_context(client, project, requirement)
    revised["definition_of_done"] = [f"{task_id}: revised behavior is independently verified."]
    changed = edit(client, task_id, restarted["revision"], revised, f"reagree-{task_id}")
    return client, original, changed, revised


def _observable_state(client, task_id: str) -> dict:
    runtime = client.runtime
    record = deepcopy(runtime.task_queries.record(task_id))
    with runtime.store.unit_of_work() as uow:
        context = deepcopy(uow.tasks.restart_context(task_id))
    with runtime.store.transaction() as database:
        receipts = tuple(
            (row["seq"], row["data"])
            for row in database.execute(
                "SELECT seq,data FROM journal WHERE task_id=? AND event='newborn.action' ORDER BY seq",
                (task_id,),
            )
        )
        events = tuple(
            (row["seq"], row["data"])
            for row in database.execute(
                "SELECT seq,data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
            )
        )
        proof = tuple(
            row["data"] for row in database.execute(
                "SELECT data FROM task_proofs WHERE task_id=?", (task_id,)
            )
        )
    return {
        "task": record,
        "context": context,
        "receipts": receipts,
        "events": events,
        "proof": proof,
        "requirements": deepcopy(runtime.requirements_commands.store.registry().to_dict()),
        "ownership": deepcopy(runtime.ownership.snapshot(client.runtime.session)),
        "head": git(runtime.cfg["git"]["repository"], "rev-parse", "HEAD"),
        "worktree_status": git(runtime.cfg["git"]["repository"], "status", "--short"),
    }


def _exercise_changed_context(project, task_id: str, requirement: str) -> str:
    client, original, changed, revised = _create_restarted(project, task_id, requirement)
    before = _observable_state(client, task_id)
    try:
        result = _ready(client, task_id, changed["revision"], f"revised-ready-{task_id}")
    except PoiseError as error:
        assert str(error) == HISTORICAL_REFUSAL
        assert _observable_state(client, task_id) == before
        return "historical-refusal"
    assert result["status"] == "available"
    current = client.runtime.task_queries.record(task_id)
    assert current["contract"]["requirements"] == revised["requirements"]
    assert current["contract"]["definition_of_done"] == revised["definition_of_done"]
    assert client.runtime.task_commands.requirements_snapshot(task_id) == revised["requirements_snapshot"]
    with client.runtime.store.unit_of_work() as uow:
        context = uow.tasks.restart_context(task_id)
    assert context["requirements_agreement"] == revised["requirements_agreement"]
    assert current["id"] == original["id"]
    assert current["sprint_id"] == original["sprint_id"]
    assert current["branch"] == original["branch"]
    assert current["worktree"] == original["worktree"]
    assert current["history"][:len(original["history"])] == original["history"]
    history = context["restart_history"]
    assert history[-1]["from_status"] == "available"
    return "accepted"


def test_probe_all_three_incident_classes(project):
    outcomes = [_exercise_changed_context(project, task_id, requirement) for task_id, requirement in INCIDENTS]
    assert len(set(outcomes)) == 1, outcomes
    if outcomes[0] == "historical-refusal":
        print("LEGACY_READY_CONTEXT_REJECTED")
    else:
        assert outcomes == ["accepted", "accepted", "accepted"]
        print("LEGACY_READY_CONTEXT_ACCEPTED")


def test_unchanged_context_uses_persisted_history(project):
    _assert_source_identity()
    task_id = "UNCHANGED-CONTEXT"
    client = tools(project, "owner-unchanged")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, "fill-unchanged")
    first = _ready(client, task_id, filled["revision"], "first-ready-unchanged")
    stored = client.runtime.task_commands.requirements_snapshot(task_id)
    restarted = restart(client, task_id, first["revision"], request_id="restart-unchanged")
    assert restarted["draft"]["requirements_snapshot"] == stored
    client.runtime.requirements_commands.apply({
        "request_id": "drift-live-after-publication",
        "expected_revision": 1,
        "operations": [{"kind": "put_requirement", "requirement": {
            "id": "FIXTURE-SYSTEM",
            "level": "system",
            "status": "obsolete",
            "text": "This live requirement changed after the Task was first published.",
        }}],
    })
    assert client.runtime.requirements_commands.store.registry().to_dict() != project["requirements_registry"].to_dict()
    assert _ready(client, task_id, restarted["revision"], "again-ready-unchanged")["status"] == "available"
    assert client.runtime.task_commands.requirements_snapshot(task_id) == stored


@pytest.mark.parametrize("invalid", (
    "text-only", "snapshot-only", "agreement-only", "missing-snapshot", "missing-agreement",
))
def test_rejects_invalid_changed_context(project, invalid):
    _assert_source_identity()
    task_id = f"INVALID-{invalid.upper()}"
    client, _, changed, revised = _create_restarted(project, task_id, f"{invalid} revised requirement.")
    original = deepcopy(project["task"])
    patch = {}
    if invalid == "text-only":
        patch = {
            "requirements_snapshot": original["requirements_snapshot"],
            "requirements_agreement": original["requirements_agreement"],
        }
    elif invalid == "snapshot-only":
        patch = {
            "requirements": original["requirements"],
            "requirements_agreement": original["requirements_agreement"],
        }
    elif invalid == "agreement-only":
        patch = {
            "requirements": original["requirements"],
            "requirements_snapshot": original["requirements_snapshot"],
        }
    elif invalid == "missing-snapshot":
        patch = {"requirements_snapshot": None}
    elif invalid == "missing-agreement":
        patch = {"requirements_agreement": None}
    broken = edit(client, task_id, changed["revision"], patch, f"break-{invalid}")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError):
        _ready(client, task_id, broken["revision"], f"invalid-ready-{invalid}")
    assert _observable_state(client, task_id) == before


@pytest.mark.parametrize("authorization", (None, "", 17))
def test_restart_rejects_missing_or_invalid_legacy_authority(project, authorization):
    _assert_source_identity()
    task_id = "INVALID-AUTHORITY"
    client = tools(project, "owner-authority")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {
        **complete_patch(project, task_id), "goal_type": "development",
    }, "fill-authority")
    first = _ready(client, task_id, filled["revision"], "ready-authority")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError):
        restart(
            client, task_id, first["revision"],
            request_id=f"restart-invalid-authority-{authorization}",
            authorization=authorization,
        )
    assert _observable_state(client, task_id) == before


def test_stale_revision_rejects_before_changed_context_preflight(project):
    _assert_source_identity()
    task_id = "STALE-REVISION"
    client, _, changed, _ = _create_restarted(project, task_id, "Revised exact contract.")
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError, match="revision"):
        _ready(client, task_id, changed["revision"] - 1, "stale-revision-ready")
    assert _observable_state(client, task_id) == before


def test_changed_context_rejects_stale_live_registry(project):
    _assert_source_identity()
    task_id = "STALE-LIVE-REGISTRY"
    client, _, changed, _ = _create_restarted(project, task_id, "Revised exact contract.")
    client.runtime.requirements_commands.apply({
        "request_id": "stale-live-registry-update",
        "expected_revision": 1,
        "operations": [{"kind": "put_requirement", "requirement": {
            "id": "FIXTURE-SYSTEM", "level": "system", "status": "obsolete",
            "text": "Invalidates the new task snapshot before ready.",
        }}],
    })
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError):
        _ready(client, task_id, changed["revision"], "stale-live-ready")
    assert _observable_state(client, task_id) == before


def test_planned_restart_keeps_revision_authority(project):
    _assert_source_identity()
    task_id = "PLANNED-AUTHORITY"
    client = tools(project, "owner-planned")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], full_draft(project, task_id), "fill-planned")
    first = _ready(client, task_id, filled["revision"], "first-ready-planned")
    restarted = restart(
        client, task_id, first["revision"], request_id="restart-planned",
        authorization={"role": "reviewer", "decision": "Reviewer authorized only goal correction."},
    )
    before = _observable_state(client, task_id)
    with pytest.raises(PoiseError, match="revision policy|authorization"):
        edit(client, task_id, restarted["revision"], {
            "definition_of_done": ["Unapproved acceptance revision."],
        }, "unapproved-planned-dod")
    assert _observable_state(client, task_id) == before


def test_exact_replay_and_conflicting_request_id(project):
    _assert_source_identity()
    task_id = "REPLAY-CONTEXT"
    client = tools(project, "owner-replay")
    born = create(client, task_id)
    filled = edit(client, task_id, born["revision"], {**complete_patch(project, task_id), "goal_type": "development"}, "fill-replay")
    first = _ready(client, task_id, filled["revision"], "ready-replay")
    before = _observable_state(client, task_id)
    replayed = _ready(client, task_id, filled["revision"], "ready-replay")
    assert replayed == first
    assert _observable_state(client, task_id) == before
    with pytest.raises(PoiseError):
        _ready(client, task_id, filled["revision"] + 1, "ready-replay")
    assert _observable_state(client, task_id) == before


def _run_probe() -> int:
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = pytest.main(["-q", "-s", f"{__file__}::test_probe_all_three_incident_classes"])
    if status != 0:
        sys.stderr.write(output.getvalue() + errors.getvalue())
        return 1
    red = "LEGACY_READY_CONTEXT_REJECTED" in output.getvalue()
    green = "LEGACY_READY_CONTEXT_ACCEPTED" in output.getvalue()
    if red == green:
        sys.stderr.write("Probe outcome is not uniquely RED or GREEN.\n")
        return 1
    if red:
        sys.stdout.write("LEGACY_READY_CONTEXT_REJECTED\n")
        return 17
    sys.stdout.write("LEGACY_READY_CONTEXT_ACCEPTED\n")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--poise-red-green-probe"]:
        raise SystemExit(2)
    raise SystemExit(_run_probe())
