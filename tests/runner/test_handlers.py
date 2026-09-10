import pytest
from harness.modules.foundation.errors import DomainError
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
