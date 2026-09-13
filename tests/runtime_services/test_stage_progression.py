"""A durable target drives lawful transitions and stops at work or role boundaries."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise as Poise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.tasks.progression import stage_role
from poise.modules.work.domain import BUSINESS_INCOMPLETE_STATUSES
from poise.modules.workflow.domain import HandlerKind


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


@pytest.mark.parametrize("handler", tuple(HandlerKind))
def test_only_inspection_stages_belong_to_the_reviewer(handler):
    expected = "reviewer" if handler == HandlerKind.INSPECT else "executor"
    assert stage_role(handler) == expected


def test_progression_pause_statuses_are_business_incomplete():
    assert {
        "progression_work_required",
        "role_handoff_required",
        "user_acceptance_required",
    } <= BUSINESS_INCOMPLETE_STATUSES


def bootstrap(tools, task):
    return tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))


def handoff(tools, request_id="executor-to-reviewer"):
    return tools.invoke(request("handoff", {
        "request_id": request_id,
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
    progressed = state_snapshot(executor.runtime, ("executor",))
    assert progressed["execution"][0]["last_report"]["status"] == "verified"
    assert progressed["task"]["history"][-1]["event"] == "stage_progressed"
    assert not any(
        item["event"].startswith("user_accept")
        for item in progressed["task"]["history"]
    )
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
    assert {
        key: value for key, value in after["task"].items() if key != "progression"
    } == {
        key: value for key, value in before["task"].items() if key != "progression"
    }
    assert after["task"]["progression"] == {
        "request_id": "reach-code-review",
        "target_stage": "code_review",
        "status": "active",
    }
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


def test_publish_requires_separate_user_acceptance_after_role_handoff(project):
    stages = configure_progression(project)
    publication = deepcopy(stages[-1])
    publication.update(
        id="publication",
        handler="publish",
        transitions={"complete": None},
        rework_targets=[],
        read_only=True,
        allowed_paths=[],
    )
    project["process"]["stages"][-1]["transitions"]["clear"] = "publication"
    project["process"]["stages"].append(publication)
    write_json(
        project["root"] / "config/processes/development.json",
        project["process"],
    )
    project["cfg"]["automatic_checks"][0]["by_stage"]["publication"] = []
    write_json(project["config_path"], project["cfg"])
    project["task"]["checks"]["publication"] = []
    project["task"]["evidence_plan"]["publication"] = {
        "subject_methods": {},
        "arguments": [],
        "review_arguments": [],
    }
    project["task"]["stage_contracts"].append({
        "stage_id": "publication",
        "allowed_paths": [],
        "entry_requirements": [],
        "exit_requirements": [],
    })
    executor = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"
    implementation = advance(executor, target="publication")
    Path(implementation["worktree"], "src/double.py").write_text(
        "def double(n):\n    return n * 2\n", encoding="utf-8",
    )
    assert verify(executor, result(implementation))["status"] == "verified"
    assert advance(executor, target="publication")["status"] == "role_handoff_required"
    handoff(executor)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    bootstrap(reviewer, {"id": "T1"})
    review = advance(reviewer, target="publication")
    assert review["status"] == "progression_work_required"
    review_result = result(review)
    review_result["stage_work"]["coverage"] = "Reviewed the implementation result."
    assert verify(reviewer, review_result)["status"] == "verified"
    assert advance(reviewer, target="publication")["status"] == "role_handoff_required"
    handoff(reviewer, "reviewer-to-publisher")
    publisher = WorkTools(Poise(project["config_path"], "publisher"))
    bootstrap(publisher, {"id": "T1"})

    blocked = advance(publisher, target="publication")

    assert blocked["status"] == "user_acceptance_required"
    assert publisher.runtime.current_task()["stage_index"] == 2
    record = publisher.runtime.task_queries.record("T1")
    assert record["status"] == "verified"
    assert not any(item["event"].startswith("user_accept") for item in record["history"])
    entered = publisher.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert entered["stage"] == "publication"
    assert publisher.runtime.task_queries.record("T1")["history"][-1]["event"] == (
        "user_accept_and_continue"
    )
