"""Same-stage restart preserves registry history and requires fresh checks."""
from copy import deepcopy
import json
from pathlib import Path
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


def test_ready_preserves_stage_identities_and_replays_without_checks(project, verification_calls):
    configure_public_registry_case(project)
    # This fixture replaces its Task-owned methods, without project-mandated checks.
    project["cfg"]["automatic_checks"][0]["by_stage"] = {
        stage["id"]: [] for stage in project["workflow"]["stages"]
    }
    fields = [
        "workflow", "methods", "method_inputs", "checks", "content_contract",
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
    workflow = deepcopy(newborn["workflow"])
    workflow["content_contract"] = {"sections": [], "routes": [], "requirements": []}
    expected_stages = json.loads((Path(__file__).parents[1] / "fixtures/invalid-check-recovery.json").read_text())["restart_stage_identities"]
    stages = [stage_id for stage_id, _ in expected_stages]
    assert [stage["id"] for stage in workflow["stages"]] == stages
    patch = {
        "workflow": workflow, "methods": [], "method_inputs": [],
        "checks": {stage: [] for stage in stages},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {stage: {"subject_methods": {}, "arguments": [], "review_arguments": []} for stage in stages},
        "stage_contracts": [
            {
                "stage_id": stage["id"],
                "entry_requirements": [],
                "exit_requirements": [],
                "allowed_paths": list(stage["allowed_paths"]),
                "criteria": {
                    "requirements": [],
                    "definition_of_ready": [],
                    "definition_of_done": [],
                },
            }
            for stage in workflow["stages"]
        ],
        "decomposition": {"kind": "ordinary", "integration": None, "phases": [
            {"stage": stage, "skills": ["task-domain"], "areas": []} for stage in stages]},
    }
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "refresh-methods-same-stages",
        "expected_revision": newborn["revision"], "patch": patch, "remove": [],
    }))
    ready = ready_task(tools, "T1", edited["revision"])
    assert ready["status"] == "available"
    record = tools.runtime.task_queries.record("T1")
    assert record["workflow"] == workflow
    assert [[stage["id"], stage["purpose"]] for stage in record["workflow"]["stages"]] == expected_stages
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
    assert repeated == {**ready, "replayed": True}
    assert persisted(tools, "T1") == rows
    assert verification_calls == old_calls
    active = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert active["stage"] == "test_implementation"
    assert active["status"] == "active"
    with tools.runtime.store.transaction() as database:
        from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
        restored = SqliteTaskRepository(database).load("T1")
    with pytest.raises(DomainError, match="GREEN executable-test"):
        restored.check_registry.validate_inspection_exit()
    fresh = registration("CURRENT", "current-check", stage="test_implementation")
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
