"""Receiving the actual reviewed work must preserve ownership and provenance."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import result, verify
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.review_release_rework_helpers import (
    acquire, add_held_wip, advance, assert_current_correction, call,
    current_correction, fixture, prepare, preserve, release, review,
    saved_results, seed_older_handoff, snapshot,
)
from ownership.test_public_release import preserve_unknown_attempt, start


def test_fixture_contract_and_handoff_are_ready(project):
    case = prepare(project)
    review(case)
    assert len(case["first_results"]) == 1
    assert len(case["reviewed_results"]) == 2
    release(case["reviewer"], "fixture-ready-release")
    released = case["reviewer"].runtime.task_queries.record("T1")
    assert released["claimed_by"] is None
    assert released["status"] == "verified"
    assert released["stage_index"] == 1


@pytest.mark.parametrize("history", [
    "fresh", "repeated", "older-released-newer-resumed", "older-resumed-newer-released",
])
def test_review_release_current_history(project, history):
    case = prepare(project)
    if history == "older-released-newer-resumed":
        seed_older_handoff(case)
    review(case)
    reviewer, executor = case["reviewer"], case["executor"]
    if history == "older-resumed-newer-released":
        transferred = preserve(reviewer, "current-review-to-executor")
        assert transferred["verified"] is True
    else:
        packet, receipt = release(reviewer, "review-to-executor")
        assert receipt["replayed"] is False
    if history == "older-released-newer-resumed":
        with executor.runtime.store.unit_of_work() as unit:
            seed = case["synthetic_handoff"]
            assert unit.handoffs.get(seed["actor"], seed["request_id"]) == seed
            latest = unit.handoffs.latest_recovery_candidate("T1")
        assert latest["state"] == "resumed"
        assert latest["actor"] == "executor"
        assert latest["request_id"] == "executor-first-review"
        assert Path(seed["receipt"]["bundle_path"]).read_bytes()
        assert executor.runtime.task_queries.record("T1")["claimed_by"] is None
        assert saved_results(executor) == case["reviewed_results"]
        assert git(case["worktree"], "rev-parse", "HEAD") == case["source"][0]
    try:
        context = current_correction(case)
    except PoiseError as error:
        if history == "older-released-newer-resumed" and str(error) == "Task changed after preserved handoff":
            pytest.fail("RR-TARGET-CURRENT-HISTORY: superseded transfer blocks current reviewed work", pytrace=False)
        raise
    if history == "older-resumed-newer-released":
        before = snapshot(executor, case["worktree"], ("executor", "reviewer"))
        assert acquire(executor)["stage"] == "amend"
        assert snapshot(executor, case["worktree"], ("executor", "reviewer")) == before
    else:
        before = snapshot(executor, case["worktree"], ("executor", "reviewer"))
        replay = reviewer.invoke(packet)
        assert replay["replayed"] is True
        assert replay["before"] == receipt["before"] and replay["after"] == receipt["after"]
        assert snapshot(executor, case["worktree"], ("executor", "reviewer")) == before
        changed = deepcopy(packet)
        changed["input"]["reason"] = "A different request intent must not release the receiver."
        with pytest.raises(PoiseError, match="conflict"):
            reviewer.invoke(changed)
        assert snapshot(executor, case["worktree"], ("executor", "reviewer")) == before
    if history == "repeated":
        payload = result(context, case["data"]["amend_report"])
        payload["stage_work"] = {"resolutions": [deepcopy(case["data"]["resolution"])]}
        assert verify(executor, payload)["status"] == "verified"
        assert advance(executor)["status"] == "role_handoff_required"
        preserve(executor, "amend-to-follow-up")
        acquire(reviewer)
        follow_up = advance(reviewer)
        assert follow_up["stage"] == "follow_up"
        payload = result(follow_up, case["data"]["review_report"])
        payload["stage_work"] = {"coverage": case["data"]["review_coverage"], "findings": [],
                                 "resolution_decisions": [deepcopy(case["data"]["rejected_resolution"])]}
        assert verify(reviewer, payload)["stage_outcome"] == "changes_requested"
        release(reviewer, "second-review-to-executor")
        acquire(executor)
        second = advance(executor)
        assert_current_correction(case, second, 2)
        decisions = second["workflow"]["feedback"]["decisions"]
        assert decisions[-1]["resolution_id"] == "RR-RESOLUTION-1"
        assert decisions[-1]["decision"] == "rejected"
        assert saved_results(executor)[:2] == case["reviewed_results"]


@pytest.mark.parametrize("drift", ["worktree", "head", "bundle-missing", "bundle-changed"])
def test_current_handoff_refuses_material_drift(project, drift):
    case = prepare(project)
    review(case)
    receipt = preserve(case["reviewer"], "review-current-material")
    bundle = Path(receipt["bundle_path"])
    original_bundle = bundle.read_bytes()
    if drift == "worktree":
        (case["worktree"] / "src/double.py").write_text("changed current source\n", encoding="utf-8")
    elif drift == "head":
        git(case["worktree"], "commit", "--allow-empty", "-m", "A distinct test source commit")
    elif drift == "bundle-missing":
        bundle.unlink()
    else:
        bundle.write_bytes(b"changed test bundle\n")
    before = snapshot(case["executor"], case["worktree"], ("executor", "reviewer"))
    diagnostic = "Worktree changed since handoff" if drift in ("worktree", "head") else "bundle missing or changed"
    with pytest.raises(PoiseError, match=diagnostic):
        acquire(case["executor"])
    assert snapshot(case["executor"], case["worktree"], ("executor", "reviewer")) == before
    if drift == "bundle-missing":
        assert not bundle.exists()
    else:
        assert bundle.read_bytes() == (b"changed test bundle\n" if drift == "bundle-changed" else original_bundle)


def test_unknown_attempt_allows_context_without_rerun(project, monkeypatch):
    owner, context = start(project)
    preserve_unknown_attempt(owner, context, monkeypatch)
    with owner.runtime.store.unit_of_work() as unit:
        pending = deepcopy(unit.execution.load("T1")[0]["pending"])
    assert pending["kind"] == "check_attempt"
    unfinished = Path(context["worktree"]) / "unfinished.note"
    unfinished.write_bytes(b"keep unknown-attempt work\n")
    release(owner, "unknown-to-receiver")
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Receiving unknown work must never rerun the command")

    monkeypatch.setattr(receiver.runtime.check_runner, "run", unexpected_run)
    received = acquire(receiver)
    assert received["task"] == "T1" and received["stage"] == "tests"
    with receiver.runtime.store.unit_of_work() as unit:
        assert unit.execution.load("T1")[0]["pending"] == pending
    before = snapshot(receiver, Path(context["worktree"]), ("release-owner", "receiver"))
    with pytest.raises(PoiseError, match="unknown|pending|attempt|исход|неизвест"):
        verify(receiver, result(received, "No recovery authority was supplied."))
    assert calls == []
    assert unfinished.read_bytes() == b"keep unknown-attempt work\n"
    assert snapshot(receiver, Path(context["worktree"]), ("release-owner", "receiver")) == before


@pytest.mark.parametrize("claim", ["task-claim", "dependent-worktree-claim"])
def test_receiving_bootstrap_refuses_foreign_ownership(project, claim):
    case = prepare(project)
    review(case)
    release(case["reviewer"], "reviewed-owner-guard")
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    if claim == "task-claim":
        current_correction(case)
        holder = case["executor"]
        assert holder.runtime.task_queries.record("T1")["claimed_by"] == "executor"
    else:
        holder = WorkTools(WorkPoise(project["config_path"], "holder"))
        holder.runtime.ownership.acquire_worktree("T1")
        assert holder.runtime.task_queries.record("T1")["claimed_by"] is None
        ownership = holder.runtime.ownership.snapshot("holder")
        assert ownership.task_id is None and ownership.worktree_task_id == "T1"
    add_held_wip(case)
    actors = ("executor", "reviewer", "holder", "receiver")
    before = snapshot(holder, case["worktree"], actors)
    with pytest.raises(PoiseError, match="live|another|owned|owner"):
        acquire(receiver)
    assert snapshot(holder, case["worktree"], actors) == before
    for relative, expected in case["data"]["held_wip"].items():
        assert (case["worktree"] / relative).read_text(encoding="utf-8") == expected
    assert receiver.runtime.ownership.snapshot("receiver").task_id is None
    assert receiver.runtime.ownership.snapshot("receiver").worktree_task_id is None
