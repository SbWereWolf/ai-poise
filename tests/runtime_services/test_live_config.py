"""Live project configuration must not become a global Task lifecycle lock."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from harness.application.work import WorkTools
from harness.modules.foundation.errors import VersionConflict
from harness.runtime import Harness
from batch.helpers import bootstrap as bootstrap_task, request, result, verify as verify_task
from conftest import add_test, write_json
from sprints.helpers import (
    bootstrap as bootstrap_sprint,
    draft,
    publish,
    setup,
    task,
    verify as verify_sprint,
)


def change_limit(project):
    cfg = deepcopy(project["cfg"])
    cfg["limits"]["output_chars"] += 1
    project["cfg"] = cfg
    write_json(project["config_path"], cfg)


def test_bound_task_lifecycle_ignores_whole_config_digest(project):
    original = WorkTools(Harness(project["config_path"], "same-session"))
    context = bootstrap_task(original, project)
    add_test(context["worktree"])
    expected_position = context["task"], context["stage"], context["iteration"]
    original_hash = original.runtime.task_queries.record("T1")["config_hash"]

    change_limit(project)
    current = WorkTools(Harness(project["config_path"], "same-session"))
    assert current.runtime.config_hash != original_hash
    shown = current.invoke(request("show", {"queries": [{"id": "state", "kind": "task"}]}))
    state = shown["results"][0]["value"]
    assert (state["task"], state["stage"], state["iteration"]) == expected_position

    report_text = "Verified with the current project config"
    verified = verify_task(current, result(context, report_text))
    assert verified["status"] == "verified"
    assert (verified["task"], verified["stage"], verified["iteration"]) == expected_position
    section = current.invoke(request("show", {"queries": [{
        "id": "report",
        "kind": "section",
        "name": "report",
        "stage": None,
        "submission": None,
        "range": None,
    }]}))["results"][0]["value"]
    assert section["text"] == report_text
    assert current.runtime.task_queries.record("T1")["config_hash"] == original_hash


def test_accept_and_rework_ignore_whole_config_digest(project):
    first = WorkTools(Harness(project["config_path"], "accepting-session"))
    context = bootstrap_task(first, project)
    add_test(context["worktree"])
    verify_task(first, result(context))

    change_limit(project)
    accepted = WorkTools(Harness(project["config_path"], "accepting-session"))
    accepted.invoke(request("accept", {}))
    record = accepted.runtime.task_queries.record("T1")
    assert record["status"] == "accepted"
    continued = accepted.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert continued["status"] == "active"
    assert continued["stage"] == "test_review"

    second_contract = deepcopy(project["task"])
    second_contract["id"] = "T2"
    reworker = WorkTools(Harness(project["config_path"], "rework-session"))
    second = reworker.invoke(request("bootstrap", {
        "task": second_contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    add_test(second["worktree"])
    verify_task(reworker, result(second))

    change_limit(project)
    current = WorkTools(Harness(project["config_path"], "rework-session"))
    revised = current.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Repeat the verified stage under an explicit user decision",
        "rework_stage": None,
    }))
    assert revised["task"] == "T2"
    assert revised["stage"] == second["stage"]
    assert revised["iteration"] == 2


def test_handoff_and_resume_ignore_whole_config_digest(project):
    source = WorkTools(Harness(project["config_path"], "source-session"))
    context = bootstrap_task(source, project)
    add_test(context["worktree"])
    payload = result(context, "Preserve this exact WIP submission")
    first = source.invoke(request("handoff", {
        "request_id": "release-before-change",
        "reason": "Move to another agent",
        "result": payload,
        "commit_message": "test: preserve live-config WIP",
        "artifact_paths": [],
    }))
    assert first["status"] == "handed_off"

    change_limit(project)
    receiver = WorkTools(Harness(project["config_path"], "receiver-session"))
    resumed = receiver.invoke(request("bootstrap", {
        "task": {"id": "T1"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert resumed["worktree"] == context["worktree"]
    assert (resumed["stage"], resumed["iteration"]) == (context["stage"], context["iteration"])
    assert resumed["result_template"]["sections"]["report"] == "Preserve this exact WIP submission"

    change_limit(project)
    current = WorkTools(Harness(project["config_path"], "receiver-session"))
    second = current.invoke(request("handoff", {
        "request_id": "release-after-change",
        "reason": "Release after another valid config change",
        "result": resumed["result_template"],
        "commit_message": "test: preserve resumed live-config WIP",
        "artifact_paths": [],
    }))
    assert second["status"] == "handed_off"
    assert current.runtime.task_queries.record("T1")["claimed_by"] is None


def test_sprint_start_uses_current_config_without_digest_gate(project):
    setup(project)
    planner = WorkTools(Harness(project["config_path"], "planner"))
    planned = draft(planner, [task(project, "A")])
    publish(planner, planned["revision"])

    change_limit(project)
    worker = WorkTools(Harness(project["config_path"], "worker"))
    context = bootstrap_sprint(worker, "A")
    record = worker.runtime.task_queries.record("A")
    assert context["task"] == "A"
    assert record["contract"]["requirements"] == ["R-A"]
    assert Path(context["worktree"]).is_dir()


def test_current_automatic_check_mapping_applies_to_existing_task(project):
    contract = deepcopy(project["task"])
    contract["methods"].append({
        "id": "CURRENT_CONFIG_CHECK",
        "argv": [sys.executable, "-B", "-c", "print('current-config-check')"],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 10,
        "expected_exit_code": 0,
        "stdout_contains": ["current-config-check"],
        "stderr_contains": [],
    })
    original = WorkTools(Harness(project["config_path"], "live-check-session"))
    context = original.invoke(request("bootstrap", {
        "task": contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    add_test(context["worktree"])

    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = [{
        "paths": ["tests/**"],
        "by_stage": {
            "tests": ["CURRENT_CONFIG_CHECK"],
            "test_review": [],
            "implementation": [],
            "code_review": [],
        },
    }]
    project["cfg"] = cfg
    write_json(project["config_path"], cfg)

    current = WorkTools(Harness(project["config_path"], "live-check-session"))
    verified = verify_task(current, result(context))
    assert {check["method"] for check in verified["checks"]} == {"RED", "CURRENT_CONFIG_CHECK"}


def test_parallel_tasks_keep_worktrees_ownership_and_versions_after_config_change(project):
    setup(project)
    first_tools = WorkTools(Harness(project["config_path"], "worker-A"))
    planned = draft(first_tools, [task(project, "A"), task(project, "B")])
    published = publish(first_tools, planned["revision"])
    first_context = bootstrap_sprint(first_tools, "A")
    second_tools = WorkTools(Harness(project["config_path"], "worker-B"))
    second_context = bootstrap_sprint(second_tools, "B")
    assert first_context["worktree"] != second_context["worktree"]

    change_limit(project)
    current_first = WorkTools(Harness(project["config_path"], "worker-A"))
    current_second = WorkTools(Harness(project["config_path"], "worker-B"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(verify_sprint, current_first, first_context)
        two = pool.submit(verify_sprint, current_second, second_context)
        first_result, second_result = one.result(), two.result()
    assert first_result["status"] == second_result["status"] == "verified"
    assert current_first.runtime.task_queries.record("A")["claimed_by"] == "worker-A"
    assert current_second.runtime.task_queries.record("B")["claimed_by"] == "worker-B"

    with pytest.raises(VersionConflict):
        current_first.invoke(request("sprint", {
            "action": "dependencies",
            "sprint_id": "S",
            "request_id": "stale-after-config-change",
            "expected_revision": published["revision"] - 1,
            "items": [],
            "reason": "This stale revision must not replace the current graph",
        }))
