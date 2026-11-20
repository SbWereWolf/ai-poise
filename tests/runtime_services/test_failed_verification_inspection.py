"""Public regression for independent inspection of corrections to a failed candidate."""

from pathlib import Path
import sys

import pytest

from batch.helpers import request, result
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runner.helpers import decision, inspect, resolution
from runtime_services.stage_result_handoff_helpers import receiver, release
from runtime_services.test_failed_verification_retry import (
    assert_retained_failure, setup_failed_candidate, task_state, verify,
)


CORRECTIONS = (("F1", "R1"), ("F2", "R2"), ("F3", "R3"))


def operation(action, request_id, **fields):
    return request("failed_verification_inspection", {
        "action": action, "task_id": "T1", "request_id": request_id, **fields,
    })


def start(failed_check_id):
    return operation("enter", "fixture-limited-entry", failed_check_id=failed_check_id,
                     reason="Examine frozen corrections after terminal verification failure.")


def submit_limited(actor, packet):
    try:
        return actor.invoke(packet)
    except PoiseError as error:
        pytest.fail(f"Limited inspection did not admit its eligible public request: {error}",
                    pytrace=False)


class LostResponse(Exception):
    pass


def discard_response(actor, packet):
    raise LostResponse(actor.invoke(packet))


def assert_replay(actor, packet, lost):
    replay = submit_limited(actor, packet)
    assert replay["replayed"] is True
    assert {key: value for key, value in replay.items() if key != "replayed"} == {
        key: value for key, value in lost.value.args[0].items() if key != "replayed"
    }
    return replay


def failed_observations(tools, receipt_ids):
    view = tools.invoke(request("show", {"queries": [{"id": "evidence", "kind": "evidence"}]}))
    return {
        item["id"]: (item["method"], item["passed"], item["actual_exit_code"], item["stage"])
        for item in view["results"][0]["value"]["observations"] if item["id"] in receipt_ids
    }


def finish_ordinary(project, author, workspace, external_state, counter, middle_decision,
                    exit_packet, lost_exit):
    correction = author.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "src" / "fixture-repair.txt").write_text("Repaired subject.\n", encoding="utf-8")
    proposal = result(correction, "Repair the failing subject through the existing application stage.")
    if middle_decision == "rejected":
        proposal["stage_work"] = {"resolutions": [resolution("R4", "F2")]}
    assert verify(author, proposal)["status"] == "verified"
    check_context = author.invoke(request("advance", {
        "task_id": "T1", "request_id": "fixture-ordinary-verification", "target_stage": "verification",
    }))
    assert check_context["stage"] == "verification"
    before = task_state(author)
    assert_replay(author, exit_packet, lost_exit)
    assert task_state(author) == before
    with pytest.raises(PoiseError, match=r"(?i)verif|провер"):
        author.invoke(request("accept", {}))
    external_state.write_text("ready\n", encoding="utf-8")
    fresh = verify(author, result(check_context, "Verify the actual repair."))
    assert fresh["status"] == "verified"
    assert [(check["method"], check["actual_exit_code"], check["passed"])
            for check in fresh["checks"]] == [("CHECK-ONCE", 0, True)]
    assert counter.read_text() == "blocked\nblocked\nready\n"
    assert author.invoke(request("accept", {}))["status"] == "accepted"
    assert task_state(author)["stage"] == "verification"
    intent = {"task_id": "T1", "request_id": "fixture-ordinary-review", "target_stage": "review"}
    assert author.invoke(request("advance", intent))["status"] == "role_handoff_required"
    assert release(author, "fixture-ordinary-release")["status"] == "ownership_released"
    reviewer, _ = receiver(project)
    review_context = reviewer.invoke(request("advance", intent))
    review = result(review_context, "Inspect the verified repair independently.")
    review["stage_work"] = inspect(decisions=[decision("R4")] if middle_decision == "rejected" else [])
    assert verify(reviewer, review)["stage_outcome"] == "clear"
    assert reviewer.invoke(request("accept", {}))["status"] == "completed"


@pytest.mark.parametrize("middle_decision", ["accepted", "rejected"])
def test_limited_inspection_preserves_failures_and_exits_to_application(project, middle_decision):
    author, _, payload, retry, external_state, counter, workspace, revision, failed = setup_failed_candidate(
        project, correction_ids=CORRECTIONS, inspection_corrective_target="next_work",
        correction_evidence=True,
    )
    again = verify(author, payload, retry)
    assert again["status"] == "checks_failed"
    old, latest = failed["checks"][0], again["checks"][0]
    assert old["id"] != latest["id"]
    assert again["attempt"] == 2
    before = task_state(author)
    assert [item["id"] for item in before["feedback"]["pending_resolutions"]] == ["R1", "R2", "R3"]
    assert latest["argv"] == old["argv"]
    assert latest["argv"][:3] == [sys.executable, "-B", "-c"]
    assert latest["timeout_selection"]["seconds"] == old["timeout_selection"]["seconds"]
    assert latest["cleanup_seconds"] == old["cleanup_seconds"]
    budget_records = {Path(check["timeout_maintenance"]["path"]):
                      Path(check["timeout_maintenance"]["path"]).read_bytes() for check in (old, latest)}
    history_ids = {old["id"], latest["id"]}
    expected_failures = {identifier: ("CHECK-ONCE", False, 23, "verification") for identifier in history_ids}
    assert failed_observations(author, history_ids) == expected_failures

    ordinary = author.invoke(request("handoff", {
        "task_id": "T1", "request_id": "fixture-blocked-ordinary-handoff",
        "reason": "Exercise ordinary failed verification handoff.", "result": payload, "artifacts": [],
    }))
    assert ordinary["status"] == "handoff_blocked"
    assert ordinary["phase"] == "verification"
    assert ordinary["release"] is None and ordinary["progression"] is None
    with pytest.raises(PoiseError, match="R1.*R2.*R3"):
        author.invoke(request("bootstrap", {
            "task": None, "decision": "rework", "feedback": "Exercise ordinary rework.",
            "rework_stage": "verification",
        }))
    entry = start(latest["id"])
    first = submit_limited(author, entry)
    assert first["status"] == "role_handoff_required"
    assert first["next_stage"] == "review"
    assert task_state(author) == before
    with pytest.raises(PoiseError):
        author.invoke(operation("decide", "fixture-self-review", entry_request_id="fixture-limited-entry",
                                decisions=[decision(item) for item in ("R1", "R2", "R3")]))
    assert task_state(author) == before

    assert release(author, "fixture-limited-release")["status"] == "ownership_released"
    reviewer, _ = receiver(project)
    with pytest.raises(LostResponse) as lost_entry:
        discard_response(reviewer, entry)
    admitted = task_state(reviewer)
    replay = assert_replay(reviewer, entry, lost_entry)
    assert replay["status"] == "limited_inspection_active"
    assert admitted["owner"] == "handoff-reviewer"
    assert admitted["stage"] == "review"
    assert admitted["attempts"] == 2
    assert admitted["feedback"]["pending_resolutions"] == before["feedback"]["pending_resolutions"]
    assert task_state(reviewer) == admitted
    assert failed_observations(reviewer, history_ids) == expected_failures
    with pytest.raises(PoiseError, match=r"(?i)request.*(conflict|intent)|intent.*conflict"):
        reviewer.invoke(operation("enter", "fixture-limited-entry", failed_check_id=latest["id"],
                                  reason="A different otherwise valid reason."))
    with pytest.raises(PoiseError):
        reviewer.invoke(request("accept", {}))
    with pytest.raises(PoiseError):
        reviewer.invoke(operation("exit", "fixture-incomplete-exit", entry_request_id="fixture-limited-entry"))
    assert task_state(reviewer) == admitted

    agreed = [decision("R1"), decision("R2", middle_decision), decision("R3")]
    decisions = operation("decide", "fixture-limited-decisions", entry_request_id="fixture-limited-entry",
                          decisions=agreed)
    with pytest.raises(LostResponse) as lost_decisions:
        discard_response(reviewer, decisions)
    reviewed = task_state(reviewer)
    decision_replay = assert_replay(reviewer, decisions, lost_decisions)
    assert decision_replay["decisions"] == agreed
    assert reviewed["feedback"]["pending_resolutions"] == []
    assert {item["id"] for item in reviewed["feedback"]["open_findings"]} == (
        {"F2"} if middle_decision == "rejected" else set()
    )
    recorded = {item["resolution_id"]: item["decision"] for item in reviewed["feedback"]["decisions"]}
    assert recorded == {"R1": "accepted", "R2": middle_decision, "R3": "accepted"}
    with pytest.raises(PoiseError, match=r"(?i)request.*(conflict|intent)|intent.*conflict"):
        reviewer.invoke(operation("decide", "fixture-limited-decisions", entry_request_id="fixture-limited-entry",
                                  decisions=[decision("R1", "rejected"), *agreed[1:]]))
    assert task_state(reviewer) == reviewed

    exit_packet = operation("exit", "fixture-limited-exit", entry_request_id="fixture-limited-entry",
                            reason="Return to the application stage for the still-failed check.")
    exit_intent = submit_limited(reviewer, exit_packet)
    assert exit_intent["status"] == "role_handoff_required"
    assert exit_intent["next_stage"] == "next_work"
    assert release(reviewer, "fixture-limited-reviewer-release")["status"] == "ownership_released"
    author.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    with pytest.raises(LostResponse) as lost_exit:
        discard_response(author, exit_packet)
    exited = task_state(author)
    assert_replay(author, exit_packet, lost_exit)
    assert task_state(author) == exited
    changed_exit = operation("exit", "fixture-limited-exit", entry_request_id="fixture-limited-entry",
                             reason="A different otherwise valid repair reason.")
    with pytest.raises(PoiseError, match=r"(?i)request.*(conflict|intent)|intent.*conflict"):
        author.invoke(changed_exit)
    assert task_state(author) == exited
    assert exited["stage"] == "next_work" and exited["owner"] == "handoff-author"
    assert exited["status"] == "active"
    assert exited["feedback"] == reviewed["feedback"]
    assert_retained_failure(author, old)
    assert_retained_failure(author, latest)
    assert failed_observations(author, history_ids) == expected_failures
    assert {path: path.read_bytes() for path in budget_records} == budget_records
    assert counter.read_text() == "blocked\nblocked\n"
    assert (git(workspace, "rev-parse", "HEAD"), git(workspace, "rev-parse", "HEAD^{tree}")) == revision
    with pytest.raises(PoiseError):
        author.invoke(request("accept", {}))
    finish_ordinary(project, author, workspace, external_state, counter, middle_decision,
                    exit_packet, lost_exit)


@pytest.mark.parametrize("invalid", [
    "stale-receipt", "missing-receipt", "unknown-outcome", "foreign-owner", "source-drift",
    "no-corrective-edge", "changed-correction-evidence",
])
def test_limited_inspection_refuses_invalid_inputs_without_effects(project, monkeypatch, invalid):
    interruption = monkeypatch if invalid == "unknown-outcome" else None
    author, _, _, retry, _, counter, workspace, _, failed = setup_failed_candidate(
        project, interrupt=interruption, correction_ids=CORRECTIONS,
        inspection_corrective_target="verification" if invalid == "no-corrective-edge" else "next_work",
        correction_evidence=invalid == "changed-correction-evidence",
    )
    check_id = retry["failed_check_id"]
    actor = author
    if invalid == "stale-receipt":
        check_id = "fixture-nonexistent-failed-receipt"
    if invalid == "missing-receipt":
        Path(failed["checks"][0]["stderr"]).unlink()
    if invalid == "foreign-owner":
        actor = WorkTools(WorkPoise(project["config_path"], "fixture-foreign-owner"))
    if invalid == "source-drift":
        git(workspace, "commit", "--allow-empty", "-m", "fixture: change candidate commit")
    if invalid == "changed-correction-evidence":
        context = author.invoke(request("bootstrap", {
            "task": None, "decision": None, "feedback": None, "rework_stage": None,
        }))
        proof = Path(context["task_root"]) / "artifacts" / "fixture-corrections.txt"
        assert proof.read_text(encoding="utf-8") == "Frozen evidence for R1, R2 and R3.\n"
        proof.write_text("Mismatched bytes.\n", encoding="utf-8")
    before = task_state(author)
    refusal = {
        "stale-receipt": r"(?i)failed.*(receipt|batch)",
        "missing-receipt": r"(?i)(receipt|evidence).*(missing|changed|unavailable)",
        "unknown-outcome": r"(?i)unknown.*outcome|pending.*(check|attempt)|неизвестен исход",
        "foreign-owner": r"(?i)owner|владен",
        "source-drift": r"(?i)source|candidate|commit|revision",
        "no-corrective-edge": r"(?i)corrective|destination|route",
        "changed-correction-evidence": r"(?i)(correction|proposal).*(evidence|artifact)|(evidence|artifact).*(changed|mismatch)",
    }[invalid]
    with pytest.raises(PoiseError, match=refusal):
        actor.invoke(start(check_id))
    assert task_state(author) == before
    assert counter.read_text() == "blocked\n"
