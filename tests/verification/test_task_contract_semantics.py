"""Task 0114 semantics survive retirement of one-time delivery fixtures."""
import json
from pathlib import Path
import sys

import pytest
from conftest import WorkPoise, bind_task_requirements, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.definition import validate_creation
from tests.verification.test_schema_diagnostics import creation_fixture, decomposition_policy
from tests.verification.test_plan_contract import method

ROOT = Path(__file__).resolve().parents[2]


def definition(goal_type="development"):
    intent, _ = creation_fixture()
    task = intent["task"]
    process = json.loads((ROOT / "config/catalogue/process-templates" / f"{goal_type}.json").read_text())
    task.update(id="CONTRACT", goal_type=goal_type, goal="Verify synthetic Task contract semantics.")
    if goal_type == "documentation":
        task.pop("executable_obligations")
    entry = process["route"]["entry"]
    task["methods"] = [method("BASELINE", plan={
        "responsibility": "Guard the existing repository before produced changes.",
        "change_surface": [], "red_stages": [], "green_stages": [entry], "red_failure": None,
    })]
    task["method_inputs"] = [{
        "method_id": "BASELINE", "repository_inputs": [], "future_outputs": [],
        "reference_profile": {"runner": "python", "parser": "inline-no-path-arguments", "version": 1},
    }]
    stages = [stage["id"] for stage in process["stages"]]
    task["checks"] = {stage: ["BASELINE"] if stage == entry else [] for stage in stages}
    task["evidence_plan"] = {stage: {
        "arguments": [], "review_arguments": [],
        "subject_methods": {"BASELINE": {"exit_codes": [0], "stdout_contains": ["OK"], "stderr_contains": []}} if stage == entry else {},
    } for stage in stages}
    requirements = process["content_contract"]["requirements"]
    task["stage_contracts"] = [{
        "stage_id": stage["id"], "allowed_paths": stage["allowed_paths"],
        "entry_requirements": [r["id"] for r in requirements if r["phase"] == "pre" and stage["id"] in r["stages"]],
        "exit_requirements": [r["id"] for r in requirements if r["phase"] == "post" and stage["id"] in r["stages"]],
    } for stage in process["stages"]]
    task["decomposition"]["phases"] = [{"stage": stage, "skills": ["workflow"], "areas": []} for stage in stages]
    if goal_type == "documentation":
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage: [] for stage in stages}
        for stage in process["stages"]:
            plan = task["evidence_plan"][stage["id"]]
            plan["subject_methods"] = {}
            if stage["handler"] == "check":
                plan["arguments"] = [{
                    "id": stage["id"] + "-argument", "kind": "logical",
                    "phase": "prepare", "observation_methods": [],
                }]
    return task, process


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_baseline_guard_domain_contract(goal_type):
    task, process = definition(goal_type)
    validated = validate_creation(task, process, [], decomposition_policy())
    assert validated["contract"] == task
    if goal_type == "development":
        assert task["methods"][0]["verification_plan"]["change_surface"] == []
        assert task["checks"][process["route"]["entry"]] == ["BASELINE"]
    else:
        assert task["methods"] == []
        assert "executable_obligations" not in task


def test_produced_result_cannot_disguise_itself_as_empty_surface_baseline():
    task, process = definition()
    entry = process["route"]["entry"]
    task["methods"][0]["verification_plan"]["green_stages"] = ["implementation"]
    task["checks"][entry] = []
    task["checks"]["implementation"] = ["BASELINE"]
    with pytest.raises(DomainError, match="route entry"):
        validate_creation(task, process, [], decomposition_policy())


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_obligations_field_follows_exact_process_schema(goal_type):
    task, process = definition(goal_type)
    if goal_type == "development":
        del task["executable_obligations"]
    else:
        task["executable_obligations"] = []
    with pytest.raises(DomainError, match="executable_obligations"):
        validate_creation(task, process, [], decomposition_policy())


@pytest.mark.parametrize("goal_type", ["development", "documentation"])
def test_public_creation_preserves_baseline_and_obligations(project, goal_type):
    task, process = definition(goal_type)
    cfg = project["cfg"]
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999}, "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    cfg["task_decomposition"] = decomposition_policy()
    cfg["processes"] = {goal_type: f"config/processes/{goal_type}.json"}
    write_json(project["root"] / cfg["processes"][goal_type], process)
    write_json(project["config_path"], cfg)
    bind_task_requirements(task, project["requirements_registry"])
    task.pop("id")
    runtime = WorkPoise(project["config_path"], "synthetic-contract-owner")
    tools = WorkTools(runtime)
    context = tools.invoke({"operation": "bootstrap", "input": {
        "task": {"request_id": "synthetic-contract-creation", "task": task},
        "decision": None, "feedback": None, "rework_stage": None,
    }, "messages": []})
    assert context["status"] == "active"
    registry = tools.invoke({"operation": "show", "input": {
        "queries": [{"id": "registry", "kind": "verification_registry"}],
    }, "messages": []})["results"][0]["value"]
    assert registry["executable_obligations"] == task.get("executable_obligations", [])
    if goal_type == "documentation":
        assert registry["current"] == []
        return
    baseline = next(item["method"] for item in registry["current"] if item["method"]["id"] == "BASELINE")
    assert baseline["verification_plan"]["change_surface"] == []
    assert baseline["verification_plan"]["green_stages"] == [process["route"]["entry"]]


def test_trace_can_be_planned_but_is_required_before_code():
    from poise.modules.content_requirements.domain import ContentPolicy, ContentSnapshot
    stages = ("verification_planning", "test_implementation", "implementation")
    contract = {
        "sections": [],
        "routes": [{"id": "task-verification", "requirements": ["Synthetic requirement."], "points": [
            {"id": "red_method", "kind": "method", "fields": {}, "write_stages": ["verification_planning", "test_implementation"]},
            {"id": "green_method", "kind": "method", "fields": {}, "write_stages": ["verification_planning", "implementation"]},
        ]}],
        "requirements": [
            {"id": "red-before-test-code", "kind": "trace", "route": "task-verification", "point": "red_method", "stages": ["test_implementation"], "phase": "pre", "field_equals": {}},
            {"id": "green-before-production-code", "kind": "trace", "route": "task-verification", "point": "green_method", "stages": ["implementation"], "phase": "pre", "field_equals": {}},
        ],
    }
    policy = ContentPolicy.from_layers({"sections": [], "routes": [], "requirements": []}, contract, stages, ("Synthetic requirement.",), ("PLANNED_RED", "PLANNED_GREEN"), ())
    empty = ContentSnapshot((), ())
    assert policy.evaluate("verification_planning", "pre", empty, ()).passed
    for stage in stages[1:]:
        assert not policy.evaluate(stage, "pre", empty, ()).passed
    planned = policy.apply(empty, "verification_planning", {}, {"task-verification": {"red_method": "PLANNED_RED", "green_method": "PLANNED_GREEN"}})
    for stage in stages[1:]:
        assert policy.evaluate(stage, "pre", planned, ()).passed


if __name__ == "__main__":
    if sys.argv[1:] != ["regression"]:
        raise SystemExit("usage: test_task_contract_semantics.py regression")
    raise SystemExit(pytest.main([__file__, "-q"]))
