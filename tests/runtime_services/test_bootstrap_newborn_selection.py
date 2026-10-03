"""The current-Task selector must understand newborn before executable stages."""

from copy import deepcopy

import pytest

from batch.helpers import configure, request, result, verify
from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.modules.foundation.errors import DomainError, PoiseError
from runtime_services.test_task_version_projection import (
    _configure_single_stage,
    _result,
)


def bootstrap(tools, task=None, decision=None, feedback=None):
    return tools.invoke(request("bootstrap", {
        "task": task,
        "decision": decision,
        "feedback": feedback,
        "rework_stage": None,
    }))


def restart_to_newborn(project):
    configure(project)
    tools = WorkTools(WorkPoise(project["config_path"], "executor"))
    initial = bootstrap(tools, project["task"])
    task_id = initial["task"]
    restarted = tools.invoke(request("task", {
        "action": "restart",
        "request_id": "restart-for-newborn-selector",
        "task_id": task_id,
        "expected_version": tools.runtime.current_task()["version"],
        "reason": "Exercise the public current-newborn selector.",
        "authorization": "Fixture user authorizes restart of this unfinished Task.",
    }))
    assert restarted["status"] == "newborn"
    return tools, task_id, restarted


def assert_newborn_projection(value, saved):
    assert value["id"] == saved["id"]
    assert value["status"] == "newborn"
    assert value["revision"] == saved["revision"]
    assert value["draft"] == saved["draft"]
    assert value["history"] == saved["history"]
    assert value["worktree"] == saved["worktree"]
    assert "version" not in value


def test_restarted_current_newborn_matches_explicit_selection(project):
    tools, task_id, restarted = restart_to_newborn(project)
    explicit = bootstrap(tools, {"id": task_id})
    before = deepcopy(tools.runtime.task_queries.record(task_id))

    current = bootstrap(tools)

    assert_newborn_projection(explicit, before)
    assert_newborn_projection(current, before)
    assert current["restart_history"] == restarted["restart_history"]
    assert tools.runtime.task_queries.record(task_id) == before


def test_fresh_current_newborn_is_repeatable_without_state_change(project):
    configure(project)
    tools = WorkTools(WorkPoise(project["config_path"], "executor"))
    created = tools.invoke(request("task", {
        "action": "create",
        "request_id": "create-fresh-newborn",
        "task_id": "DRAFT",
        "sprint_id": None,
    }))
    assert created["status"] == "newborn"
    explicit = bootstrap(tools, {"id": "DRAFT"})
    before = deepcopy(tools.runtime.task_queries.record("DRAFT"))

    first = bootstrap(tools)
    second = bootstrap(tools)

    for value in (explicit, first, second):
        assert_newborn_projection(value, before)
    assert tools.runtime.task_queries.record("DRAFT") == before


@pytest.mark.parametrize("decision,feedback", [
    ("continue", None),
    ("rework", "A newborn has no verified stage to rework."),
])
def test_newborn_decisions_are_domain_refusals_without_mutation(
    project, decision, feedback,
):
    tools, task_id, _ = restart_to_newborn(project)
    before = deepcopy(tools.runtime.task_queries.record(task_id))

    with pytest.raises(PoiseError, match="newborn|чернов|этап"):
        bootstrap(tools, decision=decision, feedback=feedback)

    assert tools.runtime.task_queries.record(task_id) == before


def test_taskless_and_available_selection_keep_their_existing_routes(project):
    tools = WorkTools(WorkPoise(project["config_path"], "executor"))
    taskless = bootstrap(tools)
    assert taskless["status"] == "read_only"
    assert taskless["task"] is None
    assert "version" not in taskless

    tools, task_id, restarted = restart_to_newborn(project)
    ready = tools.invoke(request("task", {
        "action": "ready",
        "request_id": "ready-restarted-selector-task",
        "task_id": task_id,
        "expected_revision": restarted["revision"],
    }))
    assert ready["status"] == "available"
    assert bootstrap(tools)["status"] == "read_only"
    assert bootstrap(tools, {"id": task_id})["status"] == "active"


def test_active_verified_and_accepted_current_task_paths_are_preserved(project):
    configure(project)
    tools = WorkTools(WorkPoise(project["config_path"], "executor"))
    context = bootstrap(tools, project["task"])
    assert bootstrap(tools)["status"] == "active"
    add_test(context["worktree"])
    assert verify(tools, result(context))["status"] == "verified"
    assert bootstrap(tools)["status"] == "verified"
    tools.invoke(request("accept", {}))
    assert bootstrap(tools)["status"] == "accepted"


def test_completed_task_is_terminal_and_current_selector_becomes_taskless(project):
    task = _configure_single_stage(project)
    tools = WorkTools(WorkPoise(project["config_path"], "executor"))
    context = bootstrap(tools, task)
    assert verify(tools, _result(context))["status"] == "verified"
    completed = bootstrap(tools, decision="continue")
    assert completed["status"] == "completed"
    assert bootstrap(tools)["status"] == "read_only"
    assert bootstrap(tools, {"id": task["id"]})["status"] == "completed"


def test_malformed_selector_and_conflicting_decision_preserve_current_draft(project):
    tools, task_id, _ = restart_to_newborn(project)
    before = deepcopy(tools.runtime.task_queries.record(task_id))

    with pytest.raises((DomainError, PoiseError)):
        bootstrap(tools, task=[task_id])
    with pytest.raises(PoiseError, match="разные входы"):
        bootstrap(tools, task={"id": task_id}, decision="continue")

    assert tools.runtime.task_queries.record(task_id) == before
