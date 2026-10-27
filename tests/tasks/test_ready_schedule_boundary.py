"""Real ready admission reports schedule faults without mutating Task state."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from conftest import git
from poise.modules.foundation.errors import PoiseError
from tasks.test_newborn_lifecycle import create, edit, task_action, tools


DATA = json.loads((Path(__file__).parents[1] / "verification/fixtures/ready_schedule_cases.json").read_text())


def prepare(project, patch=None):
    project["cfg"]["automatic_checks"] = []
    client = tools(project, "schedule-diagnostic-owner")
    born = create(client, "SCHEDULE")
    body = deepcopy(project["task"])
    body.update(id="SCHEDULE", methods=[], method_inputs=[],
                checks={stage["id"]: [] for stage in project["workflow"]["stages"]},
                evidence_plan={stage["id"]: {"subject_methods": {}, "arguments": [],
                                            "review_arguments": []}
                               for stage in project["workflow"]["stages"]})
    body.update(deepcopy(patch or {}))
    filled = edit(client, "SCHEDULE", born["revision"], body, "fill-schedule")
    foreign = tools(project, "foreign-task-owner")
    create(foreign, "FOREIGN")
    return client, filled, body


def state(client, project):
    with client.runtime.store.unit_of_work() as unit:
        database = unit.tasks.db
        rows = tuple(tuple(row) for row in database.execute(
            "SELECT id,status,claimed_by,version,stage_index,iteration,current_submission_id,metadata "
            "FROM tasks ORDER BY id"))
        events = tuple(tuple(row) for row in database.execute(
            "SELECT task_id,event,data FROM journal WHERE task_id=? ORDER BY seq", ("SCHEDULE",)))
    app = project["app"]
    return {
        "tasks": rows, "events": events,
        "head": git(app, "rev-parse", "HEAD"),
        "index": (app / ".git/index").read_bytes(),
        "foreign": (app / "foreign-note.txt").read_bytes(),
        "config": Path(project["config_path"]).read_bytes(),
        "worktrees": tuple(sorted(str(p) for p in project["root"].rglob("worktrees/*"))),
    }


def ready(client, revision, request_id="ready-schedule"):
    return task_action(client, action="ready", task_id="SCHEDULE",
                       expected_revision=revision, request_id=request_id)


@pytest.mark.parametrize("case", DATA["ready_diagnostics"], ids=lambda case: case["id"])
def test_diagnostics_ready_reports_field_and_preserves_task(project, case):
    patch = deepcopy(case["patch"])
    if case["id"] == "stage-value":
        patch["checks"] = {stage["id"]: [] for stage in project["workflow"]["stages"]}
        patch["checks"]["tests"] = None
    client, filled, _ = prepare(project, patch)
    (project["app"] / "foreign-note.txt").write_bytes(b"foreign bytes must remain\n")
    before = state(client, project)
    try:
        ready(client, filled["revision"])
    except Exception as error:
        observed = error
    else:
        pytest.fail("READY_INVALID_SCHEDULE_ACCEPTED")
    assert state(client, project) == before
    assert isinstance(observed, PoiseError), "READY_DOMAIN_DIAGNOSTIC_MISSING"
    text = str(observed)
    assert text.startswith(case["field"]), "READY_FIELD_DIAGNOSTIC_MISSING"
    for token in case["tokens"] + DATA["guidance_tokens"]:
        assert token in text, "READY_REMEDY_DIAGNOSTIC_MISSING"


def test_preservation_ready_can_correct_schedule_then_replay_success(project):
    client, filled, _ = prepare(project, {"checks": {}})
    (project["app"] / "foreign-note.txt").write_bytes(b"foreign bytes must remain\n")
    before = state(client, project)
    with pytest.raises(PoiseError):
        ready(client, filled["revision"])
    assert state(client, project) == before
    fixed = edit(client, "SCHEDULE", filled["revision"],
                 {"checks": {stage["id"]: [] for stage in project["workflow"]["stages"]}},
                 "correct-schedule")
    first = ready(client, fixed["revision"], "ready-corrected")
    assert {key: first[key] for key in ("status", "ready", "claimed_by")} == {
        "status": "available", "ready": True, "claimed_by": None}
    after = state(client, project)
    replay = ready(client, fixed["revision"], "ready-corrected")
    assert replay["replayed"] is True
    assert {key: value for key, value in replay.items() if key != "replayed"} == first
    assert state(client, project) == after
    assert after["foreign"] == b"foreign bytes must remain\n"
    assert after["config"] == before["config"]
    assert after["head"] == before["head"]
    assert next(row for row in after["tasks"] if row[0] == "FOREIGN") == next(
        row for row in before["tasks"] if row[0] == "FOREIGN")


@pytest.mark.parametrize("field", ["methods", "checks", "method_inputs"])
def test_preservation_required_creation_field_cannot_be_removed(project, field):
    client, filled, _ = prepare(project)
    (project["app"] / "foreign-note.txt").write_bytes(b"foreign bytes must remain\n")
    before = state(client, project)
    with pytest.raises(PoiseError, match=field):
        task_action(client, action="edit", task_id="SCHEDULE",
                    request_id="remove-required-field", expected_revision=filled["revision"],
                    patch={}, remove=[field])
    assert state(client, project) == before
