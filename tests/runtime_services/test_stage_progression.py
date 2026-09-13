"""A durable target drives lawful transitions and stops at work or role boundaries."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise as Poise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


def configure_progression(project):
    process = deepcopy(project["process"])
    stages = [
        deepcopy(process["stages"][0]),
        deepcopy(process["stages"][2]),
        deepcopy(process["stages"][3]),
    ]
    stages[0].update(
        handler="produce",
        transitions={"complete": stages[1]["id"]},
        rework_targets=[stages[0]["id"]],
    )
    stages[1].update(
        handler="produce",
        transitions={"complete": stages[2]["id"]},
        rework_targets=[stages[1]["id"]],
    )
    stages[2].update(
        handler="inspect",
        transitions={"clear": None, "changes_requested": stages[1]["id"]},
        rework_targets=[stages[2]["id"]],
    )
    process["stages"] = stages
    process["route"]["entry"] = stages[0]["id"]
    project["process"] = process
    write_json(project["root"] / "config/processes/development.json", process)
    task = deepcopy(project["task"])
    task["checks"] = {
        stages[0]["id"]: ["RED"],
        stages[1]["id"]: ["GREEN"],
        stages[2]["id"]: ["GREEN"],
    }
    task["evidence_plan"] = {
        stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
        for stage in stages
    }
    task["stage_contracts"] = [{
        "stage_id": stage["id"],
        "allowed_paths": list(stage["allowed_paths"]),
        "entry_requirements": [],
        "exit_requirements": [],
    } for stage in stages]
    project["task"] = task
    return stages


def advance(tools, target="code_review"):
    return tools.invoke(request("advance", {
        "request_id": "reach-code-review",
        "task_id": "T1",
        "target_stage": target,
    }))


def bootstrap(tools, task):
    return tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))


def handoff(tools):
    return tools.invoke(request("handoff", {
        "request_id": "executor-to-reviewer",
        "reason": "The requested progression reached its reviewer boundary.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))


def test_advances_same_role_then_requires_real_handoff_before_reviewer_stage(project):
    configure_progression(project)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"

    implementation = advance(executor)

    assert implementation["status"] == "progression_work_required"
    assert implementation["stage"] == "implementation"
    assert implementation["progression"] == {
        "request_id": "reach-code-review",
        "target_stage": "code_review",
        "status": "active",
    }
    Path(implementation["worktree"], "src/double.py").write_text(
        "def double(n):\n    return n * 2\n", encoding="utf-8",
    )
    assert verify(executor, result(implementation))["status"] == "verified"

    boundary = advance(executor)

    assert boundary["status"] == "role_handoff_required"
    assert boundary["stage"] == "implementation"
    assert boundary["next_stage"] == "code_review"
    assert boundary["from_role"] == "executor"
    assert boundary["to_role"] == "reviewer"
    assert executor.runtime.current_task()["status"] == "verified"
    handoff(executor)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    resumed = bootstrap(reviewer, {"id": "T1"})
    assert resumed["progression"] == boundary["progression"]

    reached = advance(reviewer)

    assert reached["status"] == "progression_target_reached"
    assert reached["stage"] == "code_review"
    assert reached["target_stage"] == "code_review"
    assert reviewer.runtime.current_task()["claimed_by"] == "reviewer"
    version = reviewer.runtime.current_task()["version"]
    assert advance(reviewer)["status"] == "progression_target_reached"
    assert reviewer.runtime.current_task()["version"] == version


def test_progression_rejects_foreign_owner_pending_and_unreachable_target(project):
    configure_progression(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    first = bootstrap(owner, project["task"])
    contender = WorkTools(Poise(project["config_path"], "contender"))
    with pytest.raises(PoiseError, match="owned|ownership|session"):
        advance(contender)

    data = owner.runtime.current_task()
    data["pending"] = "checks"
    owner.runtime.store.save(data)
    with pytest.raises(PoiseError, match="pending|unknown external"):
        advance(owner)
    data = owner.runtime.current_task()
    data["pending"] = None
    owner.runtime.store.save(data)

    with pytest.raises(PoiseError, match="reachable|ordinary transitions"):
        advance(owner, target="missing-stage")
    assert owner.runtime.current_task()["stage_index"] == 0


def test_progression_request_identity_rejects_a_different_target(project):
    configure_progression(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    bootstrap(tools, project["task"])
    assert advance(tools)["status"] == "progression_work_required"

    with pytest.raises(PoiseError, match="request.*conflict|another.*target"):
        advance(tools, target="implementation")
