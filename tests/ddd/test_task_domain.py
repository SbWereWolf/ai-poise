from poise.modules.evidence.domain import EvidencePlan
import pytest
from poise.modules.content.domain import SectionRule
from poise.modules.tasks.domain import Task, StageSpec, TaskStageContracts
from poise.modules.foundation.errors import DomainError


def new_task(task_id, stages, actor):
    from poise.modules.content_requirements.domain import ContentPolicy
    from poise.modules.verification.domain import CheckRegistry
    empty = {"sections": [], "routes": [], "requirements": []}
    ids = tuple(s.stage_id for s in stages)
    policy = ContentPolicy.from_layers(empty, empty, ids, (), (),
                                      tuple(sorted({r.name for s in stages for r in s.rules})))
    registry = CheckRegistry.from_task([], {s.stage_id: [] for s in stages}, ids)
    from poise.modules.workflow.domain import RouteDefinition
    cfg={"route":{"entry":ids[0] if ids else "absent"},
         "stages":[{"id":sid,"handler":"produce", "role": "executor","transitions":{"complete":ids[i+1] if i+1<len(ids) else None},
                    "rework_targets":[sid],"read_only":False,"allowed_paths":[]} for i,sid in enumerate(ids)]}
    route = RouteDefinition.from_process(cfg)
    contracts = TaskStageContracts.parse([
        {"stage_id": stage_id, "allowed_paths": [],
         "entry_requirements": [], "exit_requirements": []}
        for stage_id in ids
    ], route, policy)
    return Task.new(task_id, stages, actor, policy, registry, route, EvidencePlan.parse(
        {s["id"]:{"subject_methods":{},"arguments":[],"review_arguments":[]} for s in cfg["stages"]},
        {s["id"]:s["handler"] for s in cfg["stages"]}, {s["id"]:[] for s in cfg["stages"]}), contracts)


def task():
    return new_task("T1", (
        StageSpec("write", (SectionRule("report", "template", "strip", True),)),
        StageSpec("inspect", (SectionRule("verdict", "template", "strip", True),)),
    ), "S1")


def prepared(t=None):
    t = task() if t is None else t
    return t.submit("S1", {"report":"first result"}, (), "feat: first", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]})


def verified():
    t = prepared().task
    return t.mark_verified("S1", t.state.submission_digest, ()).task


def test_submit_changes_candidate_not_verification_state():
    before = task(); change = prepared(before)
    assert before.state.version == 0
    assert before.state.submission_digest is None
    assert change.task.state.status == "active"
    assert change.task.state.version == 1
    assert change.submission.sections[0].content == "first result"
    assert change.task.state.submission_digest == change.submission.digest


def test_identical_current_payload_is_idempotent():
    t = prepared().task
    again = prepared(t)
    assert again.task == t
    assert again.submission is None
    assert again.events == ()


def test_return_to_previous_text_is_new_layer_not_historical_dedup():
    first = prepared().task
    second = first.submit("S1", {"report":"second"}, (), "feat: first", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]}).task
    third = prepared(second)
    assert third.submission is not None
    assert third.task.state.version == second.state.version + 1
    assert third.task.state.submission_digest == first.state.submission_digest


def test_verified_is_not_accepted_and_does_not_advance():
    t = verified()
    assert (t.state.status, t.state.stage_index, t.state.iteration) == ("verified", 0, 1)
    assert t.state.claimed_by == "S1"


def test_cannot_verify_without_submission_or_with_wrong_digest():
    with pytest.raises(DomainError): task().mark_verified("S1", "unknown", ())
    with pytest.raises(DomainError): prepared().task.mark_verified("S1", "stale", ())


def test_accept_and_continue_are_different_actions():
    t = verified()
    accepted = t.accept("S1", False).task
    assert (accepted.state.status, accepted.state.stage_index) == ("accepted", 0)
    assert accepted.accept("S1", False).task == accepted
    next_stage = accepted.accept("S1", True).task
    assert (next_stage.state.status, next_stage.state.stage_index) == ("active", 1)
    assert next_stage.state.submission_digest is None


def test_unverified_task_cannot_be_accepted():
    with pytest.raises(DomainError): task().accept("S1", True)


def test_full_process_completes_only_after_final_user_decision():
    t = verified().accept("S1", True).task
    t = t.submit("S1", {"verdict":"accepted"}, (), "", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]}).task
    t = t.mark_verified("S1", t.state.submission_digest, ()).task
    assert t.state.status == "verified"
    done = t.accept("S1", False).task
    assert done.state.status == "completed"
    assert done.state.claimed_by is None
    reopened = done.rework("S2", "Уточнить verdict").task
    assert (reopened.state.status, reopened.state.iteration, reopened.state.claimed_by) == ("active",2,"S2")


def test_rework_preserves_old_snapshot_and_requires_feedback():
    old = verified()
    new = old.rework("S1", "Уточнить результат").task
    assert old.state.status == "verified"
    assert new.state.iteration == 2
    assert new.state.submission_digest is None
    with pytest.raises(DomainError): old.rework("S1", " ")


def test_cancel_skips_completion_requirements_but_requires_reason():
    old = task()
    with pytest.raises(DomainError): old.cancel("S1", "")
    cancelled = old.cancel("S1", "Пользователь отменил").task
    assert cancelled.state.status == "cancelled"
    assert cancelled.state.claimed_by is None
    assert old.state.status == "active"


def test_wrong_owner_and_mutation_after_verified_are_rejected():
    with pytest.raises(DomainError, match="^Задача связана с другой сессией$"): task().submit("S2", {"report":"x"}, (), "", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]})
    current = verified()
    with pytest.raises(DomainError, match="^Результат принимается только в активный этап текущей сессии$"): current.submit("S1", {"report":"x"}, (), "", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]})


def test_route_is_data_not_goal_type_branching():
    t = new_task("DOC", (StageSpec("editorial", (SectionRule("text","draft","exact",True),)),), "writer")
    t = t.submit("writer", {"text":"document"}, (), "docs: document", {"sections":[],"routes":[],"requirements":[]}, {}, [], {}, {"phase":"prepare","arguments":[],"decisions":[]}).task
    t = t.mark_verified("writer", t.state.submission_digest, ()).task
    assert t.accept("writer", False).task.state.status == "completed"


def test_malformed_route_fails_before_work():
    with pytest.raises(DomainError): new_task("T", (), "S")
    s = StageSpec("s", ())
    with pytest.raises(DomainError): new_task("T", (s,s), "S")


def scope_final_review(publish=False):
    from runner.helpers import task as routed_task, verify as routed_verify, inspect as packet, stage
    from runner.test_handlers import scope_process
    cfg = scope_process()
    if publish:
        next(s for s in cfg["stages"] if s["id"] == "examine")["transitions"]["clear"] = "release"
        cfg["stages"].append(stage("release", "publish", {"complete": None}, True, [], ["build"]))
    t = routed_task(cfg)
    for work in ({}, packet(), {}):
        t = routed_verify(t, work).accept("S", True).task
    return routed_verify(t, packet())


def scope_global_book(pending):
    from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
    findings = (Finding("EARLY", "result", "Unfixed earlier result", "Counterexample", "screen", 1),)
    resolutions = ((Resolution("EARLY-R", "EARLY", "Uninspected fix", "Proof", "prepare", 2),)
                   if pending else ())
    return FeedbackBook(findings, resolutions, ())


@pytest.mark.parametrize("pending", [False, True])
def test_scope_global_terminal_gate_preserves_unresolved_history(pending):
    from dataclasses import replace
    before = replace(scope_final_review(), feedback=scope_global_book(pending))
    with pytest.raises(DomainError, match="^Нельзя завершить задачу с открытыми внутренними находками$"):
        before.accept("S", False)
    assert before.state.status == "verified"
    assert tuple(f.id for f in before.feedback.open_findings) == ("EARLY",)
    assert tuple(r.id for r in before.feedback.pending_resolutions) == (("EARLY-R",) if pending else ())


@pytest.mark.parametrize("pending", [False, True])
def test_scope_publish_entry_refuses_global_obligations_before_transition(pending):
    from dataclasses import replace
    before = replace(scope_final_review(publish=True), feedback=scope_global_book(pending))
    with pytest.raises(DomainError, match="(?i)finding|resolution|обязатель|наход|исправлен"):
        before.accept("S", True)
    assert before.stage.stage_id == "examine"
    assert before.state.status == "verified"
    assert before.feedback == scope_global_book(pending)


@pytest.mark.parametrize("pending", [False, True])
def test_scope_publish_submission_refuses_global_obligations_before_effect_intent(pending):
    from dataclasses import replace
    from runner.helpers import submit as routed_submit
    before = scope_final_review(publish=True).accept("S", True).task
    before = replace(before, feedback=scope_global_book(pending))
    with pytest.raises(DomainError, match="(?i)finding|resolution|обязатель|наход|исправлен"):
        routed_submit(before, {"target_ref": "refs/heads/main", "expected_commit": "a" * 40,
                              "authorization": "User accepted publication."})
    assert before.state.submission_digest is None
    assert before.progress.stage_work is None
    assert before.action_assessment is None


def test_scope_public_restart_replay_preserves_feedback_and_unblocks_earlier_inspection(project):
    from copy import deepcopy
    from conftest import write_json, WorkPoise, git
    from poise.application.work import WorkTools
    from batch.helpers import request, result as public_result, verify as public_verify, bootstrap
    from runtime_services.test_task_restart import restart, immutable_audit_rows
    from runner.test_runner_paths import setup_project, edit
    from runner.test_handlers import scope_process
    from runner.helpers import inspect as packet, finding

    setup_project(project, "development")
    process = scope_process()
    process["goal_type"] = "development"
    for node in process["stages"]:
        node["role"] = "executor"  # Identity separation has its own existing tests.
    write_json(project["root"] / "config/processes/development.json", process)
    contract = deepcopy(project["task"])
    ids = [s["id"] for s in process["stages"]]
    contract["checks"] = {sid: ["TARGETED"] for sid in ids}
    contract["methods"][0]["verification_plan"]["green_stages"] = ids
    contract["evidence_plan"] = {sid: {"subject_methods": {}, "arguments": [], "review_arguments": []} for sid in ids}
    contract["stage_contracts"] = [{"stage_id": s["id"], "allowed_paths": s["allowed_paths"],
                                  "entry_requirements": [], "exit_requirements": []} for s in process["stages"]]
    contract["decomposition"]["phases"] = [{"stage": sid, "skills": ["workflow"], "areas": []} for sid in ids]
    write_json(project["task_path"], contract)
    runtime = WorkPoise(project["config_path"], "S1")
    tools = WorkTools(runtime)
    context = bootstrap(tools, {**project, "task": contract})
    edit(context, "development", "Controlled subject baseline\n")
    for work in ({}, packet(), {}):
        payload = public_result(context)
        payload["stage_work"] = work
        assert public_verify(tools, payload)["status"] == "verified"
        context = runtime.bootstrap(decision="continue")
    payload = public_result(context)
    payload["stage_work"] = packet([finding("CODE-1"), finding("CODE-2"), finding("CODE-3")])
    assert public_verify(tools, payload)["stage_outcome"] == "changes_requested"
    book = runtime.task_commands.workflow_context("T1")["feedback"]
    assert [(f["id"], f["stage"], f["iteration"]) for f in book["findings"]] == [
        ("CODE-1", "examine", 1), ("CODE-2", "examine", 1), ("CODE-3", "examine", 1)]
    from pathlib import Path
    from poise.common import PoiseError
    before_ref = git(project["app"], "rev-parse", "main")
    before_source = git(Path(context["worktree"]), "rev-parse", "HEAD")
    before_audit = immutable_audit_rows(runtime, "T1")
    with pytest.raises(PoiseError):
        tools.invoke(request("integrate", {"request_id": "scope-unfinished-integration", "task_id": "T1",
            "expected_source_commit": before_source, "expected_target_commit": before_ref,
            "authorization": "User authorizes integration after all required obligations are resolved.",
            "resolutions": []}))
    assert git(project["app"], "rev-parse", "main") == before_ref
    assert git(Path(context["worktree"]), "rev-parse", "HEAD") == before_source
    assert immutable_audit_rows(runtime, "T1") == before_audit
    old_version = runtime.task_queries.record("T1")["version"]
    restarted = restart(tools, "T1", old_version, request_id="scope-restart")
    assert restarted["status"] == "newborn"
    audit = immutable_audit_rows(runtime, "T1")
    replay = restart(tools, "T1", old_version, request_id="scope-restart")
    assert replay["replayed"] is True
    assert immutable_audit_rows(runtime, "T1") == audit
    with pytest.raises(PoiseError):
        tools.invoke(request("task", {"action": "ready", "request_id": "scope-stale-ready",
            "task_id": "T1", "expected_revision": restarted["revision"] + 99}))
    assert immutable_audit_rows(runtime, "T1") == audit
    assert tools.invoke(request("task", {"action": "ready", "request_id": "scope-ready",
        "task_id": "T1", "expected_revision": restarted["revision"]}))["status"] == "available"
    context = tools.invoke(request("bootstrap", {"task": {"id": "T1"}, "decision": None,
                                                 "feedback": None, "rework_stage": None}))
    assert context["stage"] == "prepare"
    assert context["workflow"]["feedback"] == book
    payload = public_result(context)
    payload["stage_work"] = {}
    assert public_verify(tools, payload)["status"] == "verified"
    context = runtime.bootstrap(decision="continue")
    payload = public_result(context)
    payload["stage_work"] = packet()
    inspected = public_verify(tools, payload)
    assert inspected["stage_outcome"] == "clear"
    assert inspected["next_stage"] == "build"
    assert runtime.task_commands.workflow_context("T1")["feedback"] == book


@pytest.mark.parametrize("pending", [False, True])
def test_scope_public_publish_refuses_before_local_effect_and_keeps_refs(project, monkeypatch, pending):
    from dataclasses import replace
    from pathlib import Path
    from conftest import git
    from actions.helpers import setup, verify as public_verify, result as public_result, advance, inspect as public_inspect
    from poise.application.actions import PlanCommands
    from poise.common import PoiseError
    from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
    runtime, context, plan, base = setup(project, conflict=False)
    assert public_verify(runtime, public_result(context, {
        "plan": plan, "phase": "prepare", "resolutions": [], "finding_resolutions": []}))["status"] == "verified"
    assert public_inspect(runtime, advance(runtime))["status"] == "verified"
    context = advance(runtime)
    assert context["stage"] == "publish"
    book = FeedbackBook((Finding("GLOBAL", "result", "Unresolved", "Counterexample", "review", 1),),
        (Resolution("GLOBAL-R", "GLOBAL", "Uninspected", "Proof", "fix", 1),) if pending else (), ())
    # Explicit domain fixture at the real pre-effect boundary; no fabricated inspection or effect receipt.
    with runtime.store.unit_of_work() as unit:
        original = unit.tasks.load(context["task"])
        change = original._change("fixture_global_obligation", None, None)
        unit.tasks.save(replace(change, task=replace(change.task, feedback=book)), original.state.version)
    before_ref = git(project["app"], "rev-parse", "main")
    before_source = git(Path(context["worktree"]), "rev-parse", "HEAD")
    calls = []
    def forbidden_effect(*args, **kwargs):
        calls.append("publish_local")
        pytest.fail("Publication effect reached with globally unresolved obligations")
    monkeypatch.setattr(PlanCommands, "publish_local", forbidden_effect)
    with pytest.raises(PoiseError, match="(?i)finding|resolution|обязатель|наход|исправлен"):
        public_verify(runtime, public_result(context, {"target_ref": "refs/heads/main",
            "expected_commit": base, "authorization": "User accepted publication."}))
    assert calls == []
    assert git(project["app"], "rev-parse", "main") == before_ref
    assert git(Path(context["worktree"]), "rev-parse", "HEAD") == before_source
    assert runtime.task_commands.workflow_context(context["task"])["feedback"] == book.context()
