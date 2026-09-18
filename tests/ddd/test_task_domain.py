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
         "stages":[{"id":sid,"handler":"produce","transitions":{"complete":ids[i+1] if i+1<len(ids) else None},
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
