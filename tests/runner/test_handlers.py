import pytest
from poise.modules.foundation.errors import DomainError
from .helpers import task, verify, submit, inspect, finding, resolution, decision

def review_task():
    return verify(task(),{}).accept("S",True).task

def revise_task():
    return verify(review_task(),inspect([finding()])).accept("S",True).task

def test_produce_cannot_register_findings_or_fake_outcome():
    with pytest.raises(DomainError): submit(task(),{"outcome":"complete"})
    with pytest.raises(DomainError): submit(task(),{"findings":[finding()]})

def test_inspection_requires_coverage_and_valid_evidence():
    bad=inspect(); bad["coverage"]=" "
    with pytest.raises(DomainError): submit(review_task(),bad)
    f=finding(); f["evidence"]=""
    with pytest.raises(DomainError): submit(review_task(),inspect([f]))

def test_unknown_inspection_decision_rejected():
    t=verify(revise_task(),{"resolutions":[resolution()]}).accept("S",True).task
    with pytest.raises(DomainError): submit(t,inspect(decisions=[decision(outcome="rework")]))

def test_resolution_remains_open_until_inspected():
    t=verify(revise_task(),{"resolutions":[resolution()]})
    ctx=t.workflow_context()
    assert ctx["feedback"]["open_findings"][0]["id"]=="F1"
    assert len(ctx["feedback"]["pending_resolutions"])==1
    assert ctx["next_stage"]=="follow_up"

def test_inspection_cannot_ignore_pending_resolution():
    t=verify(revise_task(),{"resolutions":[resolution()]}).accept("S",True).task
    with pytest.raises(DomainError): submit(t,inspect())

def test_revise_requires_real_findings_and_complete_resolution_set():
    with pytest.raises(DomainError): submit(revise_task(),{"resolutions":[]})
    with pytest.raises(DomainError): submit(revise_task(),{"resolutions":[resolution(fid="unknown")]})

def test_candidate_replacement_does_not_accumulate_unverified_findings():
    t=submit(review_task(),inspect([finding("F1")]))
    t=submit(t,inspect([finding("F2")]))
    t=t.mark_verified("S",t.state.submission_digest,()).task
    assert [f["id"] for f in t.workflow_context()["feedback"]["open_findings"]]==["F2"]

def test_identical_submission_and_verify_do_not_duplicate_feedback():
    t=submit(review_task(),inspect([finding()]))
    assert submit(t,inspect([finding()]))==t
    t=t.mark_verified("S",t.state.submission_digest,()).task
    assert t.mark_verified("S",t.state.submission_digest,()).task==t
    assert len(t.workflow_context()["feedback"]["findings"])==1

def test_new_finding_during_reinspection_routes_to_another_fix():
    t=verify(revise_task(),{"resolutions":[resolution()]}).accept("S",True).task
    t=verify(t,inspect([finding("F2")],[decision()]))
    assert [f["id"] for f in t.workflow_context()["feedback"]["open_findings"]]==["F2"]
    assert t.workflow_context()["next_stage"]=="amend"

def test_repeated_finding_id_is_not_silently_overwritten():
    t=verify(revise_task(),{"resolutions":[resolution()]}).accept("S",True).task
    with pytest.raises(DomainError): submit(t,inspect([finding()],[decision()]))


# These fixtures describe result ownership through explicit corrective edges.
# Physical node order and node labels deliberately carry no semantic ordering.
def scope_process(names=("prepare", "screen", "build", "examine")):
    from .helpers import process, stage
    prepare, screen, build, examine = names
    cfg = process()
    cfg["route"] = {"entry": prepare}
    cfg["stages"] = [
        stage(examine, "inspect", {"clear": None, "changes_requested": build},
              True, [], [examine, build], role="reviewer"),
        stage(prepare, "produce", {"complete": screen}, False, ["src/**"], [prepare]),
        stage(build, "produce", {"complete": examine}, False, ["src/**"], [build]),
        stage(screen, "inspect", {"clear": build, "changes_requested": prepare},
              True, [], [screen, prepare], role="reviewer"),
    ]
    return cfg


def scope_book(early=False, pending=False, names=("prepare", "screen", "build", "examine")):
    from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
    findings = (Finding("LATE", "result", "Later defect", "Late counterexample", names[3], 3),)
    if early:
        findings += (Finding("EARLY", "result", "Earlier defect", "Early counterexample", names[1], 2),)
    resolutions = ((Resolution("LATE-R", "LATE", "Later fix", "Late proof", names[2], 4),)
                   if pending else ())
    return FeedbackBook(findings, resolutions, ())


def scoped_review(book, names=("prepare", "screen", "build", "examine")):
    from dataclasses import replace
    initial = task(scope_process(names))
    return replace(verify(initial, {}).accept("S", True).task, feedback=book)


@pytest.mark.parametrize("names", [
    ("prepare", "screen", "build", "examine"),
    ("zeta", "omega", "alpha", "beta"),
])
def test_scope_earlier_clear_preserves_three_later_findings(names):
    from poise.modules.inspection.domain import FeedbackBook, Finding
    book = FeedbackBook(tuple(Finding(fid, "result", "Late defect", "Counterexample", names[3], 3)
                              for fid in ("LATE-1", "LATE-2", "LATE-3")), (), ())
    before = scoped_review(book, names)
    after = verify(before, inspect())
    assert after.progress.outcome == "clear"
    assert after.workflow_context()["next_stage"] == names[2]
    assert after.feedback == book
    assert before.feedback == book
    assert tuple(f.id for f in after.feedback.open_findings) == ("LATE-1", "LATE-2", "LATE-3")
    submitted = submit(before, inspect())
    assert submit(submitted, inspect()) == submitted


def test_scope_new_early_finding_keeps_foreign_finding_and_requests_own_rework():
    before = scoped_review(scope_book())
    after = verify(before, inspect([finding("NEW-EARLY")]))
    assert after.progress.outcome == "changes_requested"
    assert after.workflow_context()["next_stage"] == "prepare"
    assert tuple((f.id, f.stage, f.iteration) for f in after.feedback.findings) == (
        ("LATE", "examine", 3), ("NEW-EARLY", "screen", 1))


def test_scope_producer_proposes_only_own_fix_in_mixed_book():
    from dataclasses import replace
    before = replace(task(scope_process()), feedback=scope_book(early=True))
    after = verify(before, {"resolutions": [resolution("EARLY-R", "EARLY")]})
    assert after.feedback.findings == before.feedback.findings
    assert tuple((r.id, r.finding_id, r.stage, r.iteration) for r in after.feedback.resolutions) == (
        ("EARLY-R", "EARLY", "prepare", 1),)
    assert tuple(f.id for f in after.feedback.open_findings) == ("LATE", "EARLY")
    assert before.feedback.resolutions == ()


def test_scope_foreign_pending_does_not_require_early_decision():
    before = scoped_review(scope_book(pending=True))
    after = verify(before, inspect())
    assert after.progress.outcome == "clear"
    assert after.feedback == before.feedback
    assert tuple(r.id for r in after.feedback.pending_resolutions) == ("LATE-R",)


@pytest.mark.parametrize("outcome,expected", [("accepted", "clear"), ("rejected", "changes_requested")])
def test_scope_decides_exact_own_pending_and_preserves_foreign_pending(outcome, expected):
    from dataclasses import replace
    from poise.modules.inspection.domain import Resolution
    book = scope_book(early=True, pending=True)
    book = replace(book, resolutions=book.resolutions + (
        Resolution("EARLY-R", "EARLY", "Early fix", "Early proof", "prepare", 3),))
    before = scoped_review(book)
    after = verify(before, inspect(decisions=[decision("EARLY-R", outcome)]))
    assert after.progress.outcome == expected
    assert after.feedback.findings == book.findings
    assert after.feedback.resolutions == book.resolutions
    assert tuple(r.id for r in after.feedback.pending_resolutions) == ("LATE-R",)
    assert tuple((d.resolution_id, d.decision, d.stage, d.iteration) for d in after.feedback.decisions) == (
        ("EARLY-R", outcome, "screen", 1),)
    assert tuple(f.id for f in after.feedback.open_findings) == (
        ("LATE",) if outcome == "accepted" else ("LATE", "EARLY"))


def test_scope_early_inspector_cannot_accept_foreign_pending():
    before = scoped_review(scope_book(pending=True))
    with pytest.raises(DomainError):
        submit(before, inspect(decisions=[decision("LATE-R")]))
    assert before.feedback.decisions == ()
    assert tuple(r.id for r in before.feedback.pending_resolutions) == ("LATE-R",)


def test_scope_foreign_pending_does_not_block_own_proposal():
    from dataclasses import replace
    before = replace(task(scope_process()), feedback=scope_book(early=True, pending=True))
    after = verify(before, {"resolutions": [resolution("EARLY-R", "EARLY")]})
    assert tuple(r.id for r in after.feedback.pending_resolutions) == ("LATE-R", "EARLY-R")
    assert after.feedback.findings == before.feedback.findings


@pytest.mark.parametrize("work", [
    {"resolutions": [resolution("FOREIGN-R", "LATE")]},
    {"resolutions": [resolution("LATE-R", "EARLY")]},
    {"resolutions": [resolution("EARLY-R", "EARLY"), resolution("OTHER-R", "EARLY")]},
])
def test_scope_rejects_foreign_duplicate_id_and_duplicate_coverage_proposals(work):
    from dataclasses import replace
    before = replace(task(scope_process()), feedback=scope_book(early=True, pending=True))
    with pytest.raises(DomainError):
        submit(before, work)
    assert before.state.version == 0
    assert tuple(r.id for r in before.feedback.pending_resolutions) == ("LATE-R",)


def test_scope_current_pending_cannot_be_ignored_with_foreign_pending_present():
    from dataclasses import replace
    from poise.modules.inspection.domain import Resolution
    book = scope_book(early=True, pending=True)
    book = replace(book, resolutions=book.resolutions + (
        Resolution("EARLY-R", "EARLY", "Early fix", "Proof", "prepare", 2),))
    before = scoped_review(book)
    with pytest.raises(DomainError):
        submit(before, inspect())
    assert tuple(r.id for r in before.feedback.pending_resolutions) == ("LATE-R", "EARLY-R")


def test_scope_generic_follow_up_keeps_rejected_origin_on_next_empty_visit():
    t = verify(revise_task(), {"resolutions": [resolution()]}).accept("S", True).task
    rejected = verify(t, inspect(decisions=[decision(outcome="rejected")]))
    assert rejected.progress.outcome == "changes_requested"
    before = rejected.feedback
    # A second real visit is reached through a correction and a rejected decision.
    amend = rejected.accept("S", True).task
    follow = verify(amend, {"resolutions": [resolution("R2")]}).accept("S", True).task
    follow = verify(follow, inspect(decisions=[decision("R2", "rejected")]))
    revisited = follow.rework("S", "Inspect rejection again", "follow_up").task
    after = verify(revisited, inspect())
    assert after.progress.outcome == "changes_requested"
    assert after.workflow_context()["next_stage"] == "amend"
    assert tuple(f.id for f in after.feedback.open_findings) == ("F1",)
    assert after.feedback == follow.feedback
    assert before.decisions[0].decision == "rejected"


def test_scope_report_correction_cannot_accept_itself():
    before = scoped_review(scope_book(early=True))
    work = inspect(decisions=[decision("EARLY-R")])
    work["resolutions"] = [resolution("EARLY-R", "EARLY")]
    with pytest.raises(DomainError, match="^Report correction cannot find/accept its own correction in the same inspection$"):
        submit(before, work)
    assert before.feedback.decisions == ()


def test_scope_unknown_origin_is_explicit_error_not_clear_or_earlier_rework():
    from poise.modules.inspection.domain import FeedbackBook, Finding
    book = FeedbackBook((Finding("ORPHAN", "result", "Defect", "Proof", "missing-node", 1),), (), ())
    before = scoped_review(book)
    with pytest.raises(DomainError, match="(?i)scope|ownership|област|влад|неизвест"):
        submit(before, inspect())
    assert before.feedback == book


def test_scope_empty_book_control_clears_and_own_later_finding_requests_correction():
    from dataclasses import replace
    before = scoped_review(scope_book())
    empty = replace(before, feedback=before.feedback.empty())
    assert verify(empty, inspect()).progress.outcome == "clear"
    late = verify(empty, inspect()).accept("S", True).task
    late = verify(late, {}).accept("S", True).task
    late = replace(late, feedback=scope_book())
    after = verify(late, inspect())
    assert after.progress.outcome == "changes_requested"
    assert after.workflow_context()["next_stage"] == "build"
    assert after.feedback == late.feedback


def test_scope_report_correction_proposes_own_finding_without_closing_foreign():
    before = scoped_review(scope_book(early=True))
    work = inspect()
    work["resolutions"] = [resolution("REPORT-R", "EARLY")]
    after = verify(before, work)
    assert after.progress.outcome == "changes_requested"
    assert tuple(f.id for f in after.feedback.open_findings) == ("LATE", "EARLY")
    assert tuple((r.id, r.finding_id, r.stage) for r in after.feedback.pending_resolutions) == (
        ("REPORT-R", "EARLY", "screen"),)
    assert after.feedback.decisions == ()


def test_scope_pending_rework_error_identifies_actual_foreign_inspector():
    from dataclasses import replace
    before = replace(verify(task(scope_process()), {}), feedback=scope_book(pending=True))
    with pytest.raises(DomainError, match="LATE-R.*examine"):
        before.rework("S", "Review retained pending obligation", "prepare")
    assert before.stage.stage_id == "prepare"
    assert before.state.status == "verified"
    assert before.feedback == scope_book(pending=True)


def test_scope_ambiguous_corrective_owner_is_explicit_error():
    cfg = scope_process()
    # Two unrelated result inspectors must not silently claim one correction.
    next(s for s in cfg["stages"] if s["id"] == "screen")["transitions"]["changes_requested"] = "build"
    with pytest.raises(DomainError, match="(?i)scope|ownership|ambig|област|влад|неоднознач"):
        before = task(cfg)
        verify(before, {})
