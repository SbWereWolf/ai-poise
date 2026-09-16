"""A durable target drives lawful transitions and stops at work or role boundaries."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise as Poise, add_test, git, write_json
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
    stage_ids = {stage["id"] for stage in stages}
    task["decomposition"]["phases"] = [
        phase for phase in task["decomposition"]["phases"]
        if phase["stage"] in stage_ids
    ]
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
        evidence = deepcopy(unit.evidence.list_for("T1"))
    with runtime.store.transaction() as database:
        progression = [tuple(row) for row in database.execute(
            "SELECT event,data FROM journal WHERE task_id='T1' "
            "AND event LIKE 'progression.%' ORDER BY seq"
        )]
    task = deepcopy(runtime.task_queries.record("T1"))
    worktree = Path(task["worktree"])
    return {
        "task": task,
        "execution": execution,
        "evidence": evidence,
        "git": {
            "head": git(worktree, "rev-parse", "HEAD"),
            "tree": git(worktree, "rev-parse", "HEAD^{tree}"),
        },
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
    assert boundary["review_identity"]["executor_actor"] == "executor"
    assert boundary["review_identity"]["candidate_actor"] == "executor"
    assert boundary["review_identity"]["distinct_actor"] is False
    assert boundary["review_identity"]["source"] == "verified_task_event"
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


def test_resumed_reviewer_crosses_boundary_after_ownership_only_suffix(project):
    configure_progression(project)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"
    implementation = advance(executor)
    Path(implementation["worktree"], "src/double.py").write_text(
        "def double(n):\n    return n * 2\n", encoding="utf-8",
    )
    assert verify(executor, result(implementation))["status"] == "verified"
    boundary = advance(executor)
    assert boundary["status"] == "role_handoff_required"
    assert handoff(executor)["status"] == "handed_off"

    first_reviewer = WorkTools(Poise(project["config_path"], "first-reviewer"))
    resumed = bootstrap(first_reviewer, {"id": "T1"})
    assert resumed["progression"] == boundary["progression"]
    before_suffix = state_snapshot(
        first_reviewer.runtime,
        ("executor", "first-reviewer", "second-reviewer"),
    )
    first_reviewer.runtime.ownership.release_task("T1")
    second_reviewer = WorkTools(Poise(project["config_path"], "second-reviewer"))
    acquired = bootstrap(second_reviewer, {"id": "T1"})
    after_suffix = state_snapshot(
        second_reviewer.runtime,
        ("executor", "first-reviewer", "second-reviewer"),
    )

    assert acquired["progression"] == boundary["progression"]
    assert after_suffix["execution"] == before_suffix["execution"]
    assert after_suffix["progression_journal"] == before_suffix["progression_journal"]
    assert after_suffix["evidence"] == before_suffix["evidence"]
    assert after_suffix["git"] == before_suffix["git"]
    for field in ("status", "stage_index", "iteration", "last_report"):
        assert after_suffix["task"][field] == before_suffix["task"][field]

    reached = advance(second_reviewer)

    if reached["status"] == "role_handoff_required":
        pytest.fail("ENDLESS_ROLE_HANDOFF_REQUIRED")
    assert reached["status"] == "progression_target_reached"
    assert reached["stage"] == "code_review"
    assert reached["progression"] == {
        **boundary["progression"],
        "status": "reached",
    }
    completed = state_snapshot(
        second_reviewer.runtime,
        ("executor", "first-reviewer", "second-reviewer"),
    )
    assert completed["execution"][0]["last_report"] == (
        after_suffix["execution"][0]["last_report"]
    )
    assert completed["evidence"] == after_suffix["evidence"]
    assert completed["git"] == after_suffix["git"]
    before_replay = state_snapshot(
        second_reviewer.runtime,
        ("executor", "first-reviewer", "second-reviewer"),
    )
    assert advance(second_reviewer)["status"] == "progression_target_reached"
    assert state_snapshot(
        second_reviewer.runtime,
        ("executor", "first-reviewer", "second-reviewer"),
    ) == before_replay


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
    project["task"]["decomposition"]["phases"].append({
        "stage": "publication", "skills": ["task-domain"], "areas": [],
    })
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


def _released_review_boundary(project):
    configure_progression(project)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"
    implementation = advance(executor)
    Path(implementation["worktree"], "src/double.py").write_text(
        "def double(n):\n    return n * 2\n", encoding="utf-8",
    )
    assert verify(executor, result(implementation))["status"] == "verified"
    assert advance(executor)["status"] == "role_handoff_required"
    assert handoff(executor)["status"] == "handed_off"
    return executor


@pytest.mark.parametrize("entry", ["bootstrap", "acquire"])
def test_shared_executor_identity_cannot_acquire_reviewer_boundary(project, entry):
    executor = _released_review_boundary(project)
    before = state_snapshot(executor.runtime, ("executor",))
    same_native_actor = WorkTools(Poise(project["config_path"], "executor"))
    with pytest.raises(PoiseError, match="distinct native reviewer session"):
        if entry == "bootstrap":
            bootstrap(same_native_actor, {"id": "T1"})
        else:
            same_native_actor.runtime.ownership.acquire_task("T1")
    assert state_snapshot(executor.runtime, ("executor",)) == before


def test_reviewer_relay_does_not_erase_the_original_executor_identity(project):
    executor = _released_review_boundary(project)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    bootstrap(reviewer, {"id": "T1"})
    assert advance(reviewer)["stage"] == "code_review"
    assert handoff(reviewer, "reviewer-session-archived")["status"] == "handed_off"
    before = state_snapshot(reviewer.runtime, ("executor", "reviewer"))
    with pytest.raises(PoiseError, match="distinct native reviewer session"):
        bootstrap(executor, {"id": "T1"})
    assert state_snapshot(reviewer.runtime, ("executor", "reviewer")) == before
    resumed = WorkTools(Poise(project["config_path"], "reviewer"))
    assert bootstrap(resumed, {"id": "T1"})["stage"] == "code_review"
    assert advance(resumed)["status"] == "progression_target_reached"


def test_review_guard_rejects_legacy_same_actor_progression_without_mutation(project):
    executor = _released_review_boundary(project)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    bootstrap(reviewer, {"id": "T1"})
    with reviewer.runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET claimed_by='executor' WHERE id='T1'")
        db.execute("UPDATE sessions SET task_id=NULL WHERE id='reviewer'")
        db.execute("UPDATE sessions SET task_id='T1' WHERE id='executor'")
    before = state_snapshot(executor.runtime, ("executor", "reviewer"))
    with pytest.raises(PoiseError, match="distinct native reviewer session"):
        advance(executor)
    assert state_snapshot(executor.runtime, ("executor", "reviewer")) == before


def test_same_actor_bootstrap_rejects_before_worktree_entry_checks(project, monkeypatch):
    executor = _released_review_boundary(project)
    before = state_snapshot(executor.runtime, ("executor",))

    def unexpected_entry(_):
        pytest.fail("Reviewer rejection must precede entry/worktree processing")

    monkeypatch.setattr(executor.runtime, "_require_entry", unexpected_entry)
    with pytest.raises(PoiseError, match="distinct native reviewer session"):
        executor.runtime.bootstrap(task={"id": "T1"})
    assert state_snapshot(executor.runtime, ("executor",)) == before


def test_legacy_self_review_rejects_before_verification_workspace(project, monkeypatch):
    executor = _released_review_boundary(project)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    bootstrap(reviewer, {"id": "T1"})
    review = advance(reviewer)
    with reviewer.runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET claimed_by='executor' WHERE id='T1'")
        db.execute("UPDATE sessions SET task_id=NULL WHERE id='reviewer'")
        db.execute("UPDATE sessions SET task_id='T1' WHERE id='executor'")
    before = state_snapshot(executor.runtime, ("executor", "reviewer"))

    def unexpected_workspace(_):
        pytest.fail("Reviewer rejection must precede workspace or check execution")

    monkeypatch.setattr(executor.runtime, "_verification_workspace", unexpected_workspace)
    with pytest.raises(PoiseError, match="distinct native reviewer session"):
        executor.runtime.verify(result(review))
    assert state_snapshot(executor.runtime, ("executor", "reviewer")) == before


def test_completed_review_returns_to_original_executor_without_false_self_review(project):
    process = deepcopy(project["process"])
    for name, following, rework in [
        ("test_review", "implementation", "tests"),
        ("code_review", None, "implementation"),
    ]:
        stage = next(item for item in process["stages"] if item["id"] == name)
        stage.update(handler="inspect", transitions={"clear": following, "changes_requested": rework})
    write_json(project["root"] / "config/processes/development.json", process)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"
    assert advance(executor)["status"] == "role_handoff_required"
    handoff(executor)
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    bootstrap(reviewer, {"id": "T1"})
    inspection = advance(reviewer)
    assert inspection["stage"] == "test_review"
    payload = result(inspection)
    payload["stage_work"]["coverage"] = "Reviewed the regression and its observed RED."
    assert verify(reviewer, payload)["status"] == "verified"
    assert advance(reviewer)["status"] == "role_handoff_required"
    handoff(reviewer, "reviewer-back-to-original-executor")
    bootstrap(executor, {"id": "T1"})
    implementation = advance(executor)
    assert implementation["stage"] == "implementation"
    Path(implementation["worktree"], "src/double.py").write_text(
        "def double(n):\n    return n * 2\n", encoding="utf-8",
    )
    assert verify(executor, result(implementation))["status"] == "verified"
    assert advance(executor)["status"] == "role_handoff_required"


def test_legacy_review_provenance_is_explicit_and_ambiguity_is_not_guessed(project):
    executor = _released_review_boundary(project)
    with executor.runtime.store.transaction() as db:
        db.execute("UPDATE task_events SET data=json_remove(data,'$.actor') "
                   "WHERE task_id='T1' AND json_extract(data,'$.event')='verified'")
    with executor.runtime.store.unit_of_work() as unit:
        identity = unit.tasks.review_identity("T1")
    assert identity["source"] == "legacy_verify_start"
    assert identity["executor_actor"] == "executor"
    with executor.runtime.store.transaction() as db:
        db.execute("INSERT INTO journal(at,session_id,task_id,event,data) "
                   "SELECT at,'unrelated-later-actor',task_id,event,data FROM journal "
                   "WHERE task_id='T1' AND event='verify.start' "
                   "AND json_extract(data,'$.stage')='implementation'")
    with executor.runtime.store.unit_of_work() as unit:
        identity = unit.tasks.review_identity("T1")
    assert identity["source"] == "unavailable"
    assert identity["executor_actor"] is None
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    before = state_snapshot(executor.runtime, ("executor", "reviewer"))
    with pytest.raises(PoiseError, match="identity provenance is unavailable"):
        bootstrap(reviewer, {"id": "T1"})
    assert state_snapshot(executor.runtime, ("executor", "reviewer")) == before
