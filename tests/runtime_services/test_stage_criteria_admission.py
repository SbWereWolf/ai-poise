"""Public reads/resumption preserve an actually admitted execution revision."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.runtime_services.stage_criteria_helpers import (
    boot, criteria, next_stage, proposal, rows, show, verify_packet, version, work,
)


def test_repeated_bootstrap_and_fresh_connection_preserve_bound_revision(project):
    tools, context = boot(project, initial_execution=True)
    admitted = show(tools)
    before = rows(tools)
    refreshed = next_stage(tools)
    assert refreshed["task"] == context["task"]
    assert refreshed["stage"] == "build"
    assert show(tools) == admitted
    assert rows(tools) == before
    reopened = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
    assert show(reopened) == admitted
    assert rows(reopened) == before


def test_design_and_observation_visits_do_not_start_future_execution(project):
    tools, context = boot(project)
    initial = show(tools)
    assert initial["execution"] == {"state": "unstarted", "epoch": 0, "bound_revision": None}
    tools.invoke(verify_packet(context))
    assert next_stage(tools)["stage"] == "assess"
    assert show(tools)["execution"] == initial["execution"]


def test_other_explicit_grant_does_not_unfreeze_started_execution(project):
    tools, context = boot(project)
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    execution = next_stage(tools)
    assert execution["stage"] == "build"
    bound = show(tools)
    tools.invoke(verify_packet(execution))
    custom = next_stage(tools)
    assert custom["stage"] == "custom"
    assert custom["purpose"] == "other"
    candidate = proposal(tools)
    before = rows(tools)
    with pytest.raises(PoiseError, match="target_started_requires_restart"):
        tools.invoke(verify_packet(custom, work(tools, "change-started", proposals=[candidate])))
    assert rows(tools) == before
    assert show(tools)["criteria"] == bound["criteria"]
    assert show(tools)["execution"] == bound["execution"]


def test_unrelated_future_stage_has_no_start_record_when_first_execution_is_admitted(project):
    tools, context = boot(project, second_execution=True)
    second = show(tools, "build_next")
    tools.invoke(verify_packet(context))
    tools.invoke(verify_packet(next_stage(tools)))
    assert next_stage(tools)["stage"] == "build"
    assert show(tools)["execution"]["state"] == "started"
    assert show(tools, "build_next") == second


def test_malformed_read_is_nonmutating_and_does_not_open_execution(project):
    tools, _ = boot(project)
    before = rows(tools)
    current = show(tools)
    query = {"id": "criteria", "kind": "stage_criteria", "stage_id": "build"}
    malformed = deepcopy(query)
    malformed["start"] = True
    with pytest.raises(PoiseError):
        tools.invoke(request("show", {"queries": [malformed]}))
    assert rows(tools) == before
    assert show(tools) == current


def test_authorized_restart_opens_new_epoch_without_losing_task_wip_or_prior_binding(project):
    tools, context = boot(project, initial_execution=True)
    old = show(tools)
    directory = Path(context["worktree"])
    source = directory / "src/double.py"
    source.write_text("def double(value):\n    return value * 3\n", encoding="utf-8")
    head = git(directory, "rev-parse", "HEAD")
    diff = git(directory, "diff", "--", "src/double.py")
    history = rows(tools)
    newborn = tools.invoke(request("task", {
        "action": "restart", "task_id": "T1", "expected_version": version(tools),
        "request_id": "restart-bound-build",
        "reason": "The accepted criterion needs a reviewed correction.",
        "authorization": "The task owner authorizes revising the bound criterion.",
    }))
    assert newborn["status"] == "newborn"
    assert newborn["task"] == "T1"
    contracts = deepcopy(newborn["draft"]["stage_contracts"])
    contracts[0]["criteria"] = criteria("Return three times the input.")
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "expected_revision": newborn["revision"],
        "request_id": "revise-restarted-build",
        "patch": {"stage_contracts": contracts}, "remove": [],
    }))
    ready = tools.invoke(request("task", {
        "action": "ready", "task_id": "T1", "expected_revision": edited["revision"],
        "request_id": "ready-restarted-build",
    }))
    assert ready["status"] == "available"
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["worktree"] == str(directory)
    current = show(tools)
    assert current["criteria"] == criteria("Return three times the input.")
    assert current["execution"]["state"] == "started"
    assert current["execution"]["epoch"] == old["execution"]["epoch"] + 1
    assert current["execution"]["bound_revision"] != old["execution"]["bound_revision"]
    assert current["history"][:len(old["history"])] == old["history"]
    after = rows(tools)
    assert after["events"][:len(history["events"])] == history["events"]
    assert after["submissions"] == history["submissions"]
    assert source.read_text(encoding="utf-8") == "def double(value):\n    return value * 3\n"
    assert git(directory, "rev-parse", "HEAD") == head
    assert git(directory, "diff", "--", "src/double.py") == diff


def test_stale_restart_cannot_unbind_execution(project):
    tools, _ = boot(project, initial_execution=True)
    old = show(tools)
    before = rows(tools)
    with pytest.raises(PoiseError):
        tools.invoke(request("task", {
            "action": "restart", "task_id": "T1", "expected_version": version(tools) + 1,
            "request_id": "stale-restart-bound-build", "reason": "Reject a stale restart.",
            "authorization": "The task owner authorizes a version-checked restart only.",
        }))
    assert show(tools) == old
    assert rows(tools) == before
