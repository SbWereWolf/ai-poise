"""A durable target drives lawful transitions and stops at work or role boundaries."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise as Poise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


def configure_progression(project, *, gated=False):
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
    if gated:
        requirement = {
            "id": "implementation-input",
            "kind": "artifact",
            "scope": "task",
            "pattern": "gate.txt",
            "minimum": 1,
            "maximum": 1,
            "stages": [stages[1]["id"]],
            "phase": "pre",
            "source": {"kind": "preexisting"},
        }
        task["content_contract"]["requirements"] = [requirement]
        task["stage_contracts"][1]["entry_requirements"] = [requirement["id"]]
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


def state_snapshot(runtime, actors=("owner", "contender")):
    with runtime.store.unit_of_work() as unit:
        execution = deepcopy(unit.execution.load("T1"))
    with runtime.store.transaction() as database:
        progression = [tuple(row) for row in database.execute(
            "SELECT event,data FROM journal WHERE task_id='T1' "
            "AND event LIKE 'progression.%' ORDER BY seq"
        )]
    return {
        "task": deepcopy(runtime.task_queries.record("T1")),
        "execution": execution,
        "ownership": {
            actor: runtime.ownership.snapshot(actor) for actor in actors
        },
        "progression_journal": progression,
    }


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
    receipt = handoff(executor)
    assert receipt["status"] == "handed_off"
    assert executor.runtime.current_task() is None
    released = executor.runtime.task_queries.record("T1")
    assert released["claimed_by"] is None
    assert executor.runtime.ownership.snapshot("executor").task_id is None
    assert executor.runtime.ownership.snapshot("executor").worktree_task_id is None
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


def test_progression_checks_next_entry_gate_before_transition_and_resumes_after_fix(project):
    configure_progression(project, gated=True)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(tools, project["task"])
    add_test(first["worktree"])
    assert verify(tools, result(first))["status"] == "verified"
    before = state_snapshot(tools.runtime, ("executor",))

    blocked = advance(tools)

    assert blocked["status"] == "broken"
    assert blocked["failure"]["kind"] == "content_requirements_failed"
    assert blocked["failure"]["phase"] == "pre"
    assert blocked["blocked_transition"] == {
        "from_stage": "tests",
        "to_stage": "implementation",
    }
    after = state_snapshot(tools.runtime, ("executor",))
    assert after["task"] == before["task"]
    assert after["execution"] == before["execution"]
    assert after["ownership"] == before["ownership"]
    assert before["progression_journal"] == []
    assert len(after["progression_journal"]) == 1
    assert after["progression_journal"][0][0] == "progression.started"
    assert tools.runtime.current_task()["stage_index"] == 0
    assert advance(tools)["status"] == "broken"
    assert state_snapshot(tools.runtime, ("executor",)) == after

    rework = tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Provide the omitted implementation input.",
        "rework_stage": None,
    }))
    gate = Path(rework["task_root"], "gate.txt")
    gate.write_text("ready\n", encoding="utf-8")
    corrected = result(rework)
    corrected["artifact_paths"] = [str(gate)]
    assert verify(tools, corrected)["status"] == "verified"

    resumed = advance(tools)

    assert resumed["status"] == "progression_work_required"
    assert resumed["stage"] == "implementation"


def test_progression_rejects_foreign_owner_without_any_state_change(project):
    configure_progression(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    bootstrap(owner, project["task"])
    contender = WorkTools(Poise(project["config_path"], "contender"))
    before = state_snapshot(owner.runtime)
    with pytest.raises(PoiseError, match="owned|ownership|session"):
        advance(contender)
    assert state_snapshot(owner.runtime) == before


def test_progression_rejects_pending_without_any_operation_state_change(project):
    configure_progression(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    bootstrap(owner, project["task"])
    data = owner.runtime.current_task()
    data["pending"] = "checks"
    owner.runtime.store.save(data)
    before = state_snapshot(owner.runtime, ("owner",))
    with pytest.raises(PoiseError, match="pending|unknown external"):
        advance(owner)
    assert state_snapshot(owner.runtime, ("owner",)) == before


def test_progression_rejects_valid_but_unreachable_target_without_mutation(project):
    configure_progression(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    first = bootstrap(owner, project["task"])
    add_test(first["worktree"])
    assert verify(owner, result(first))["status"] == "verified"
    owner.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    before = state_snapshot(owner.runtime, ("owner",))

    with pytest.raises(PoiseError, match="reachable|ordinary transitions"):
        owner.invoke(request("advance", {
            "request_id": "unreachable-tests",
            "task_id": "T1",
            "target_stage": "tests",
        }))

    assert state_snapshot(owner.runtime, ("owner",)) == before


def test_progression_rejects_unknown_target_without_mutation(project):
    configure_progression(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    bootstrap(owner, project["task"])
    before = state_snapshot(owner.runtime, ("owner",))
    with pytest.raises(PoiseError, match="reachable|ordinary transitions"):
        advance(owner, target="missing-stage")
    assert state_snapshot(owner.runtime, ("owner",)) == before


def test_progression_request_identity_rejects_a_different_target(project):
    configure_progression(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    bootstrap(tools, project["task"])
    assert advance(tools)["status"] == "progression_work_required"

    with pytest.raises(PoiseError, match="request.*conflict|another.*target"):
        advance(tools, target="implementation")
