"""Public ready must reload historical methods after a lawful route revision."""
from copy import deepcopy
import sys

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import add_test
from poise.modules.foundation.errors import DomainError
from runtime_services.test_restart_registry_preservation import (
    persisted, projection, ready_task, restart_task, verification_calls,
)
from verification.test_current_registry_mutation import (
    OBLIGATIONS, change, configure_public_registry_case,
    focused_product_registration, operation, public_tools, registration,
)


def test_ready_reloads_retired_stages_and_replays_without_checks(project, verification_calls):
    configure_public_registry_case(project)
    fields = [
        "process", "methods", "method_inputs", "checks", "content_contract",
        "evidence_plan", "stage_contracts", "decomposition",
    ]
    project["task"]["planning"] = {
        "schema": "task-planning-1", "template": None,
        "restart_revision_policy": {"reviewer": [], "user": fields},
    }
    tools = public_tools(project, "history-executor")
    context = bootstrap(tools, project)
    add_test(context["worktree"])
    payload = result(context)
    payload["method_additions"] = change(
        operation("replace", "GREEN", registration=focused_product_registration(project, OBLIGATIONS)),
        operation("remove", "CLEANUP_FULL_GREEN"),
    )
    assert verify(tools, payload)["status"] == "verified"
    original = projection(tools)
    original_rows = persisted(tools, "T1")
    old_calls = list(verification_calls)
    assert old_calls
    newborn = restart_task(tools, "T1")
    process = deepcopy(newborn["process"])
    producer = next(s for s in process["stages"] if s["id"] == "test_remediation")
    inspector = next(s for s in process["stages"] if s["id"] == "test_inspection")
    producer.update(
        handler="produce", read_only=True, allowed_paths=[],
        sections={"report": "Observe the committed result.", "test_registry": "Register current guards."},
        required_sections=["report", "test_registry"], rework_targets=["test_remediation"],
        transitions={"complete": "test_inspection"},
    )
    inspector.update(
        rework_targets=["test_remediation"],
        transitions={"changes_requested": "test_remediation", "clear": None},
    )
    process["stages"] = [producer, inspector]
    process["route"]["entry"] = "test_remediation"
    process["content_contract"] = {"sections": [], "routes": [], "requirements": []}
    stages = ["test_remediation", "test_inspection"]
    patch = {
        "process": process, "methods": [], "method_inputs": [],
        "checks": {stage: [] for stage in stages},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {stage: {"subject_methods": {}, "arguments": [], "review_arguments": []} for stage in stages},
        "stage_contracts": [{"stage_id": stage, "entry_requirements": [], "exit_requirements": [], "allowed_paths": []} for stage in stages],
        "decomposition": {"kind": "ordinary", "integration": None, "phases": [
            {"stage": stage, "skills": ["task-domain"], "areas": []} for stage in stages]},
    }
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "retire-stages",
        "expected_revision": newborn["revision"], "patch": patch, "remove": [],
    }))
    ready = ready_task(tools, "T1", edited["revision"])
    assert ready["status"] == "available"
    record = tools.runtime.task_queries.record("T1")
    assert record["process"] == process
    assert record["status"] == "available"
    assert record["claimed_by"] is None
    after = projection(tools)
    assert after["revision"] == 2
    assert after["current"] == []
    assert after["executable_obligations"] == list(OBLIGATIONS)
    assert after["history"] == original["history"] + [{"revision": 1, "entries": original["current"]}]
    assert after["requests"][:len(original["requests"])] == original["requests"]
    for key in ("results", "submissions", "evidence", "layers"):
        assert persisted(tools, "T1")[key] == original_rows[key]
    rows = persisted(tools, "T1")
    repeated = ready_task(tools, "T1", edited["revision"])
    assert {k: v for k, v in repeated.items() if k != "interaction"} == {k: v for k, v in ready.items() if k != "interaction"}
    assert persisted(tools, "T1") == rows
    assert verification_calls == old_calls
    active = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert active["stage"] == "test_remediation"
    assert active["status"] == "active"
    with tools.runtime.store.transaction() as database:
        from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
        restored = SqliteTaskRepository(database).load("T1")
    with pytest.raises(DomainError, match="GREEN executable-test"):
        restored.check_registry.validate_inspection_exit()
    fresh = registration("CURRENT", "current-check", stage="test_remediation")
    fresh["method"]["argv"][0] = sys.executable
    fresh["method"]["verification_plan"]["change_surface"] = []
    payload = result(active)
    payload["sections"]["test_registry"] = "A fresh current GREEN guard covers both unchanged fixture obligations."
    payload["method_additions"] = change(
        operation("add", "CURRENT", registration=fresh), request_id="after-ready", revision=2,
    )
    assert verify(tools, payload)["status"] == "verified"
    current = projection(tools)
    assert current["revision"] == 3
    assert [entry["method"]["id"] for entry in current["current"]] == ["CURRENT"]
    assert current["history"][:-1] == after["history"]
    assert len(verification_calls) == len(old_calls) + 1
