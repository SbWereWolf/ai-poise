"""Authoritative registry conservation at the public restart/ready boundary."""
from copy import deepcopy
import json

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import add_test
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
from poise.modules.foundation.errors import DomainError, PoiseError
from verification.test_current_registry_mutation import (
    OBLIGATIONS, change, configure_public_registry_case,
    focused_product_registration, operation, public_tools,
)


def projection(tools):
    # The public ID-addressed query remains available after ready releases ownership.
    return tools.runtime.task_queries.verification_registry("T1")


def persisted(tools, task_id):
    """Exact conservation oracle, independent of the repository deserializer."""
    statements = {
        "task": "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata FROM tasks WHERE id=?",
        "methods": "SELECT method_id,version,data FROM task_methods WHERE task_id=? ORDER BY rowid",
        "workflow": "SELECT data FROM task_workflows WHERE task_id=?",
        "execution": "SELECT data,version FROM task_execution WHERE task_id=?",
        "results": "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
        "events": "SELECT version,at,data FROM task_events WHERE task_id=? ORDER BY rowid",
        "proofs": "SELECT data FROM task_proofs WHERE task_id=?",
        "submissions": "SELECT seq,stage,iteration,digest,data FROM submissions WHERE task_id=? ORDER BY seq",
        "evidence": "SELECT id,stage,iteration,data FROM evidence WHERE task_id=? ORDER BY id",
        "layers": "SELECT submission_id,data FROM workflow_layers WHERE task_id=? ORDER BY submission_id",
    }
    with tools.runtime.store.transaction() as database:
        return {key: [tuple(row) for row in database.execute(sql, (task_id,))]
                for key, sql in statements.items()}


def setup_current(project, *, initially_empty=False, allow_method_edit=True):
    configure_public_registry_case(project)
    project["task"]["planning"] = {
        "schema": "task-planning-1", "template": None,
        "restart_revision_policy": {
            "reviewer": ["process"],
            "user": (["process", "methods", "method_inputs", "checks"]
                     if allow_method_edit else ["process"]),
        },
    }
    original = deepcopy(project["task"])
    if initially_empty:
        project["task"]["methods"] = []
        project["task"]["method_inputs"] = []
        project["task"]["checks"] = {stage["id"]: [] for stage in project["process"]["stages"]}
        project["cfg"]["automatic_checks"][0]["by_stage"] = deepcopy(project["task"]["checks"])
    tools = public_tools(project, "registry-executor")
    boot = bootstrap(tools, project)
    add_test(boot["worktree"])
    payload = result(boot)
    green = focused_product_registration(project if not initially_empty else {**project, "task": original}, OBLIGATIONS)
    if initially_empty:
        red = next(item for item in original["methods"] if item["id"] == "RED")
        operations = [
            operation("add", "RED", registration={"method": red, "stages": ["test_implementation"]}),
            operation("add", "GREEN", registration=green),
        ]
    else:
        operations = [operation("replace", "GREEN", registration=green),
                      operation("remove", "CLEANUP_FULL_GREEN")]
    payload["method_additions"] = change(*operations, request_id="current-methods")
    verified = verify(tools, payload)
    assert verified["status"] == "verified"
    current = projection(tools)
    assert current["revision"] == 1
    assert [entry["method"]["id"] for entry in current["current"]] == ["RED", "GREEN"]
    assert set(current["current"][0]) == {"method", "stages"}
    assert current["current"][1]["evidence_kind"] == "executable_test"
    assert current["current"][1]["covers"] == ["requirements[0]", "definition_of_done[0]"]
    assert [entry["request_id"] for entry in current["requests"]] == ["current-methods"]
    return tools, current


def restart_task(tools, task_id, request_id="restart-current"):
    version = tools.runtime.task_queries.record(task_id)["version"]
    return tools.invoke(request("task", {
        "action": "restart", "task_id": task_id, "request_id": request_id,
        "expected_version": version,
        "reason": "Repair the process without losing the current registry.",
        "authorization": {"role": "user", "decision": "Authorise this fixture Task restart."},
    }))


def ready_task(tools, task_id, revision, request_id="ready-current"):
    return tools.invoke(request("task", {
        "action": "ready", "task_id": task_id, "request_id": request_id,
        "expected_revision": revision,
    }))


@pytest.mark.parametrize("initially_empty", [False, True], ids=["replaced-and-deleted", "empty-creation"])
def test_process_only_restart_ready_preserves_complete_current_registry(project, initially_empty):
    tools, before = setup_current(project, initially_empty=initially_empty, allow_method_edit=False)
    task_id = project["task"]["id"]
    record = deepcopy(tools.runtime.task_queries.record(task_id))
    rows = persisted(tools, task_id)
    newborn = restart_task(tools, task_id)
    # A draft-only description update must not re-register any method.
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": task_id, "request_id": "process-description",
        "expected_revision": newborn["revision"],
        "patch": {"goal_type": "development"}, "remove": [],
    }))
    assert ready_task(tools, task_id, edited["revision"])["status"] == "available"
    after = tools.runtime.task_queries.record(task_id)
    assert projection(tools) == before
    saved = persisted(tools, task_id)
    assert saved["methods"] == rows["methods"]
    assert saved["results"] == rows["results"]
    assert saved["submissions"] == rows["submissions"]
    assert saved["evidence"] == rows["evidence"]
    assert saved["layers"] == rows["layers"]
    assert saved["events"][:len(rows["events"])] == rows["events"]
    assert after["requirements_snapshot"] == record["requirements_snapshot"]
    assert after["requirements_agreement"] == record["requirements_agreement"]
    assert after["worktree"] == record["worktree"]
    assert after["pending"] is None
    assert after["last_report"] is None
    assert after["publication"] is None
    assert after["attempts"] == 0


def test_authorised_method_replacement_preserves_unchanged_entries_and_audit(project):
    tools, before = setup_current(project)
    task_id = project["task"]["id"]
    newborn = restart_task(tools, task_id)
    # Declare an explicit replacement through the existing editable Task owner.
    methods = [deepcopy(entry["method"]) for entry in before["current"]]
    methods[1]["verification_plan"]["responsibility"] = "Prove the approved replacement regression."
    inputs = [deepcopy(value) for value in project["task"]["method_inputs"]
              if value["method_id"] in ("RED", "GREEN")]
    checks = {stage["id"]: [] for stage in project["process"]["stages"]}
    checks["test_implementation"] = ["RED"]
    checks["implementation"] = ["GREEN"]
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": task_id, "request_id": "authorised-replacement",
        "expected_revision": newborn["revision"],
        "patch": {"methods": methods, "method_inputs": inputs, "checks": checks},
        "remove": [],
    }))
    assert ready_task(tools, task_id, edited["revision"])["status"] == "available"
    after = projection(tools)
    assert after["current"][0] == before["current"][0]
    assert after["current"][1]["method"] == methods[1]
    assert after["current"][1]["evidence_kind"] == "executable_test"
    assert after["current"][1]["covers"] == ["requirements[0]", "definition_of_done[0]"]
    assert after["revision"] > before["revision"]
    assert after["history"][:len(before["history"])] == before["history"]
    assert after["requests"][:len(before["requests"])] == before["requests"]
    assert "CLEANUP_FULL_GREEN" not in [entry["method"]["id"] for entry in after["current"]]


def test_restart_and_ready_exact_request_replays_preserve_result_and_do_not_execute(project):
    tools, before = setup_current(project, initially_empty=True)
    task_id = project["task"]["id"]
    version = tools.runtime.task_queries.record(task_id)["version"]
    newborn = restart_task(tools, task_id)
    ready = ready_task(tools, task_id, newborn["revision"])
    rows = persisted(tools, task_id)
    replay = tools.invoke(request("task", {
        "action": "restart", "task_id": task_id, "request_id": "restart-current",
        "expected_version": version,
        "reason": "Repair the process without losing the current registry.",
        "authorization": {"role": "user", "decision": "Authorise this fixture Task restart."},
    }))
    assert {k: v for k, v in replay.items() if k not in ("interaction", "replayed")} == {
        k: v for k, v in newborn.items() if k not in ("interaction", "replayed")}
    assert replay["replayed"] is True
    repeated = ready_task(tools, task_id, newborn["revision"])
    assert {k: v for k, v in repeated.items() if k != "interaction"} == {
        k: v for k, v in ready.items() if k != "interaction"}
    assert persisted(tools, task_id) == rows
    assert projection(tools) == before


def test_failed_ready_rolls_back_task_registry_and_requirements(project, monkeypatch):
    tools, _ = setup_current(project)
    task_id = project["task"]["id"]
    newborn = restart_task(tools, task_id)
    rows = persisted(tools, task_id)
    query = [{"id": "requirements", "kind": "registry"}]
    requirements = deepcopy(tools.runtime.requirements_commands.query(query))
    promote = SqliteTaskRepository.promote_newborn

    def fail_after_promotion(repository, *args, **kwargs):
        promote(repository, *args, **kwargs)
        raise PoiseError("Fixture failure after Task promotion")

    monkeypatch.setattr(SqliteTaskRepository, "promote_newborn", fail_after_promotion)
    with pytest.raises(PoiseError, match="Fixture failure after Task promotion"):
        ready_task(tools, task_id, newborn["revision"])
    assert persisted(tools, task_id) == rows
    assert tools.runtime.requirements_commands.query(query) == requirements


def test_process_only_grant_rejects_unauthorised_method_edit_without_writes(project):
    tools, _ = setup_current(project, allow_method_edit=False)
    task_id = project["task"]["id"]
    newborn = restart_task(tools, task_id)
    methods = deepcopy(newborn["draft"]["methods"])
    methods[0]["verification_plan"]["responsibility"] = "An unauthorised replacement."
    rows = persisted(tools, task_id)
    with pytest.raises(DomainError, match="forbids changes"):
        tools.invoke(request("task", {
            "action": "edit", "task_id": task_id, "request_id": "unapproved-method-edit",
            "expected_revision": newborn["revision"], "patch": {"methods": methods}, "remove": [],
        }))
    assert persisted(tools, task_id) == rows


@pytest.mark.parametrize("corruption", ["missing", "null", "negative-revision"])
def test_restart_rejects_missing_or_corrupt_current_registry_atomically(project, corruption):
    tools, _ = setup_current(project)
    task_id = project["task"]["id"]
    # Corrupt only this isolated pytest fixture, never a managed live Task.
    with tools.runtime.store.transaction() as database:
        workflow = json.loads(database.execute(
            "SELECT data FROM task_workflows WHERE task_id=?", (task_id,)).fetchone()[0])
        if corruption == "missing":
            workflow.pop("registry")
        elif corruption == "null":
            workflow["registry"] = None
        else:
            workflow["registry"]["revision"] = -1
        database.execute("UPDATE task_workflows SET data=? WHERE task_id=?",
                         (json.dumps(workflow), task_id))
    rows = persisted(tools, task_id)
    with pytest.raises((DomainError, PoiseError), match="registry|реестр"):
        restart_task(tools, task_id)
    assert persisted(tools, task_id) == rows


def test_legitimately_empty_current_registry_is_preserved_without_resurrection(project):
    configure_public_registry_case(project)
    project["task"]["methods"] = []
    project["task"]["method_inputs"] = []
    project["task"]["planning"] = {
        "schema": "task-planning-1", "template": None,
        "restart_revision_policy": {"reviewer": ["process"], "user": ["process"]},
    }
    project["task"]["checks"] = {stage["id"]: [] for stage in project["process"]["stages"]}
    project["cfg"]["automatic_checks"][0]["by_stage"] = deepcopy(project["task"]["checks"])
    tools = public_tools(project, "registry-executor")
    bootstrap(tools, project)
    before = projection(tools)
    assert before["current"] == []
    assert before["revision"] == 0
    assert before["history"] == []
    assert before["requests"] == []
    newborn = restart_task(tools, project["task"]["id"])
    assert ready_task(tools, project["task"]["id"], newborn["revision"])["status"] == "available"
    assert projection(tools) == before
