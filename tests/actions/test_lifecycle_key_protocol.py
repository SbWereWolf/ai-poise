"""Literal adapter protocol, independent of the future lifecycle/key compiler."""
from copy import deepcopy
import json

import pytest

from poise.modules.tasks.newborn import NewbornTask
from .lifecycle_helpers import FIXTURES, metadata, runtime


CASES = json.loads((FIXTURES / "lifecycle_key_protocol.json").read_text())


def arrange_audit(client, task_id, case):
    """Only a disposable adapter fixture: not a claimed public restart."""
    value = metadata(client, task_id)
    if case["absent"]:
        value.pop("restart_history", None)
    elif case["id"] == "native-zero":
        native = NewbornTask.create("ZERO", None, "actor")
        changed = native.restart_draft("actor", "Reconsider native draft.", "User authorized restart.")
        assert changed.restart_history[-1]["from_version"] == 0
        value["restart_history"] = list(changed.restart_history)
    else:
        value["restart_history"] = deepcopy(case["history"])
    # A literal current version greater than every declared boundary. No native
    # restart event is invented; public owner/event cases live in lifecycle tests.
    with client.store.transaction() as database:
        database.execute("UPDATE tasks SET metadata=?,version=? WHERE id=?",
                         (json.dumps(value, ensure_ascii=False), 100, task_id))


def stored(client, task_id):
    with client.store.transaction() as database:
        runs = [tuple(row) for row in database.execute(
            "SELECT stage,iteration,version,data FROM action_runs WHERE task_id=? ORDER BY stage,iteration",
            (task_id,),
        )]
        events = [tuple(row) for row in database.execute(
            "SELECT stage,iteration,version,data FROM action_events WHERE task_id=? ORDER BY seq",
            (task_id,),
        )]
    return runs, events


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_literal_run_and_event_identity_on_create_load_and_save(project, case):
    client, context = runtime(project)
    task_id = context["task"]
    arrange_audit(client, task_id, case)
    value = json.loads((FIXTURES / "lifecycle_saved_run.json").read_text())
    expected_key = (case["stored_stage"], case["stored_iteration"])
    with client.store.unit_of_work() as unit:
        unit.actions.create(task_id, case["stage"], case["iteration"], value)
    runs, events = stored(client, task_id)
    if [row[:2] for row in runs] != [expected_key] or [row[:2] for row in events] != [expected_key]:
        pytest.fail("LIFECYCLE_KEY_CREATE_PROTOCOL_MISMATCH")
    assert [(row[2], json.loads(row[3])) for row in runs] == [(2, value)]
    assert [(row[2], json.loads(row[3])) for row in events] == [(2, value)]
    with client.store.unit_of_work() as unit:
        assert unit.actions.load(task_id, case["stage"], case["iteration"]) == value
        updated = deepcopy(value)
        updated["version"] = 3
        unit.actions.save(task_id, case["stage"], case["iteration"], updated, 2)
        assert unit.actions.load(task_id, case["stage"], case["iteration"]) == updated
    after_runs, after_events = stored(client, task_id)
    assert [row[:3] for row in after_runs] == [(*expected_key, 3)]
    assert [row[:3] for row in after_events] == [(*expected_key, 2), (*expected_key, 3)]
    assert json.loads(after_runs[0][3]) == updated
    assert [json.loads(row[3]) for row in after_events] == [value, updated]
    assert after_events[0] == events[0]


def test_increasing_literal_scopes_never_select_or_rewrite_prior_keys(project):
    client, context = runtime(project)
    task_id = context["task"]
    cases = {case["id"]: case for case in CASES}
    value = json.loads((FIXTURES / "lifecycle_saved_run.json").read_text())
    prior_runs, prior_events = [], []
    for name in ["initial-absent", "native-zero", "positive", "increasing"]:
        case = cases[name]
        arrange_audit(client, task_id, case)
        with client.store.unit_of_work() as unit:
            if unit.actions.load(task_id, "apply", 1) is not None:
                pytest.fail("LIFECYCLE_KEY_HISTORY_SELECTED")
            unit.actions.create(task_id, "apply", 1, value)
        runs, events = stored(client, task_id)
        assert all(row in runs for row in prior_runs)
        assert events[:len(prior_events)] == prior_events
        assert (case["stored_stage"], case["stored_iteration"], 2) in [row[:3] for row in runs]
        assert events[-1][:3] == (case["stored_stage"], case["stored_iteration"], 2)
        prior_runs, prior_events = runs, events
    assert len(prior_runs) == 4 and len(prior_events) == 4
    assert [json.loads(row[3]) for row in prior_runs] == [value] * 4
