"""Existing-Task consumers validate current methods without inventing declarations."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from poise.modules.foundation.errors import DomainError
from runtime_services.test_restart_late_method_inputs import claims
from runtime_services.test_restart_registry_preservation import (
    persisted, projection, ready_task, setup_current,
)
from verification.test_current_registry_mutation import public_tools


DATA = json.loads((Path(__file__).parent / "fixtures/existing_contract_inputs.json").read_text())


def independent_inspection(project, executor):
    current = executor.runtime.task_queries.record("T1")
    if current["status"] == "available":
        context = executor.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
        }))
        assert verify(executor, result(context))["status"] == "verified"
    else:
        assert current["status"] == "verified"
    version = executor.runtime.task_queries.record("T1")["version"]
    assert executor.invoke(request("release", {
        **DATA["release"], "expected_version": version,
    }))["status"] == "ownership_released"
    reviewer = public_tools(project, "contract-reviewer")
    reviewer.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    context = reviewer.invoke(request("bootstrap", {
        "task": None, "decision": "continue", "feedback": None, "rework_stage": None,
    }))
    assert context["stage"] == "test_inspection"
    assert context["workflow"]["role"] == "reviewer"
    assert context["session"] == "contract-reviewer"
    return reviewer


@pytest.mark.parametrize("initially_empty", [True, False], ids=["late-only", "mixed"])
def test_unplanned_late_ready_allows_existing_stage_contract_revision(project, initially_empty):
    executor, before = setup_current(project, initially_empty=initially_empty,
                                     with_extra=not initially_empty, unplanned=True)
    version = executor.runtime.task_queries.record("T1")["version"]
    newborn = executor.invoke(request("task", {**DATA["restart"], "expected_version": version}))
    entries = before["current"]
    declarations = deepcopy(newborn["draft"]["method_inputs"])
    edited = executor.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "materialize-unplanned-current",
        "expected_revision": newborn["revision"],
        "patch": {"methods": [entry["method"] for entry in entries]}, "remove": [],
    }))
    assert ready_task(executor, "T1", edited["revision"])["status"] == "available"
    reviewer = independent_inspection(project, executor)
    assert reviewer.runtime.task_queries.record("T1")["contract"].get("planning") is None
    before_registry = deepcopy(projection(reviewer))
    context_before = reviewer.runtime.stage_contract_context("T1")
    old = next(value for value in context_before["current"] if value["stage_id"] == "implementation")
    candidate = {**deepcopy(old), "allowed_paths": DATA["legal_scope"]}
    version = reviewer.runtime.task_queries.record("T1")["version"]
    assert reviewer.invoke(request("revise_stage_contract", {
        **DATA["revise"], "expected_version": version,
        "stage_id": "implementation", "contract": candidate,
    }))["status"] == "stage_contract_revised"
    after = reviewer.runtime.stage_contract_context("T1")
    revised = next(value for value in after["current"] if value["stage_id"] == "implementation")
    assert revised == candidate
    assert revised["allowed_paths"] == ["src/**", "docs/**"]
    assert after["history"][:-1] == context_before["history"]
    assert after["history"][-1]["old"] == old
    assert after["history"][-1]["new"] == candidate
    assert after["history"][-1]["actor"] == "contract-reviewer"
    assert projection(reviewer) == before_registry
    assert reviewer.runtime.task_queries.record("T1")["contract"]["method_inputs"] == declarations


def test_existing_contract_revision_keeps_declared_future_producer_guard(project):
    executor, _ = setup_current(project, unplanned=True)
    reviewer = independent_inspection(project, executor)
    context = reviewer.runtime.stage_contract_context("T1")
    old = next(value for value in context["current"] if value["stage_id"] == "test_implementation")
    candidate = {**deepcopy(old), "allowed_paths": DATA["invalid_scope"]}
    rows, registry, owners = persisted(reviewer, "T1"), projection(reviewer), claims(reviewer)
    version = reviewer.runtime.task_queries.record("T1")["version"]
    with pytest.raises(DomainError, match="path tests is outside allowed_paths of producer test_implementation"):
        reviewer.invoke(request("revise_stage_contract", {
            **DATA["revise"], "request_id": "reject-existing-producer-loss",
            "expected_version": version, "stage_id": "test_implementation", "contract": candidate,
        }))
    assert persisted(reviewer, "T1") == rows
    assert projection(reviewer) == registry
    assert claims(reviewer) == owners
