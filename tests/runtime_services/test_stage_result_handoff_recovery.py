"""Integration subject: exact continuation across committed real-owner effects."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from runtime_services.stage_result_handoff_helpers import (
    blocked, blocked_details, business, complete, count, handoff, interrupt_after,
    receiver, source_state, start,
)


def test_existing_unknown_receipt_control_preserves_the_real_pending_attempt(project, monkeypatch):
    tools, _, packet, counter = start(project)
    original = tools.runtime.evidence_commands.record_receipt

    def unavailable(*_args, **_kwargs):
        raise PoiseError("fixture terminal receipt persistence unavailable")

    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", unavailable)
    verification = request("verify", {"result": packet["input"]["result"], "artifacts": []})
    with pytest.raises(PoiseError, match="fixture terminal receipt"):
        tools.invoke(deepcopy(verification))
    pending = deepcopy(source_state(tools)["pending"])
    assert pending["kind"] == "check_attempt" and pending["runs"][0]["started"] is True
    assert count(counter) == "x"
    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", original)
    with pytest.raises(PoiseError, match="Unknown check outcome"):
        tools.invoke(deepcopy(verification))
    assert source_state(tools)["pending"] == pending
    assert count(counter) == "x"


@pytest.mark.parametrize("owner", ["verification", "progression", "release"])
def test_exact_continuation_does_not_repeat_a_committed_owner_effect(project, monkeypatch, owner):
    tools, _, packet, counter = start(project, mode="same_role" if owner == "progression" else "roles")
    if owner == "verification":
        calls = interrupt_after(monkeypatch, tools.runtime, "verify", "after committed verify")
    elif owner == "progression":
        calls = interrupt_after(monkeypatch, tools.runtime, "advance", "after committed advance")
    else:
        calls = interrupt_after(monkeypatch, tools.runtime.ownership.commands, "release", "after committed release")
    first = handoff(tools, packet)
    assert business(first)["status"] in ("handoff_completed", "handoff_blocked")
    if business(first)["status"] == "handoff_blocked":
        blocked(first, owner)
        blocked_details(first, "after_" + owner, identities=[packet["input"]["request_id"]])
    assert count(counter) == "x"
    resumed = handoff(tools, packet)
    complete(resumed, replayed=business(first)["status"] == "handoff_completed")
    assert calls  # The real owner was reached before injected response loss.
    assert count(counter) == "x"
    assert source_state(tools)["counts"][0] == 1
    assert source_state(tools)["stage"] == ("next_work" if owner == "progression" else "author")
    before = source_state(tools)
    complete(handoff(tools, packet), replayed=True)
    assert source_state(tools) == before


def test_release_failure_has_a_real_partial_outcome_and_retries_without_new_verification(project, monkeypatch):
    tools, _, packet, counter = start(project, mode="same_role")
    original = tools.runtime.ownership.commands.release
    attempts = []

    def refuse_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PoiseError("fixture release temporarily refused")
        return original(*args, **kwargs)

    monkeypatch.setattr(tools.runtime.ownership.commands, "release", refuse_once)
    first = blocked(handoff(tools, packet), "release")
    blocked_details(first, "release", identities=[packet["input"]["request_id"]])
    assert first["verification"]["status"] == "verified"
    assert first["progression"]["stage"] == "next_work"
    assert first["release"] is None
    assert source_state(tools)["claimed_by"] == "handoff-author"
    assert count(counter) == "x"
    complete(handoff(tools, packet))
    assert count(counter) == "x" and source_state(tools)["counts"][0] == 1


def test_response_loss_after_release_preserves_a_receiver_who_acquired_before_retry(project, monkeypatch):
    tools, _, packet, counter = start(project)
    calls = interrupt_after(monkeypatch, tools.runtime.ownership.commands, "release", "lost release acknowledgement")
    first = handoff(tools, packet)
    assert business(first)["status"] in ("handoff_completed", "handoff_blocked")
    if business(first)["status"] == "handoff_blocked":
        blocked(first, "release")
        blocked_details(first, "release_acknowledgement", identities=[packet["input"]["request_id"]])
    incoming, _ = receiver(project)
    before = source_state(incoming)
    ownership = incoming.runtime.ownership.snapshot("handoff-reviewer")
    recovered = handoff(tools, packet)
    complete(recovered, replayed=business(first)["status"] == "handoff_completed")
    assert source_state(incoming) == before
    assert incoming.runtime.ownership.snapshot("handoff-reviewer") == ownership
    assert calls and count(counter) == "x"


def test_missing_terminal_check_receipt_keeps_unknown_outcome_without_reexecution(project, monkeypatch):
    tools, _, packet, counter = start(project)
    original = tools.runtime.evidence_commands.record_receipt
    seen = []

    def lose_receipt(*args, **kwargs):
        seen.append(1)
        if len(seen) == 1:
            raise PoiseError("fixture terminal receipt persistence unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", lose_receipt)
    first = blocked(handoff(tools, packet), "verification")
    assert first["progression"] is None and first["release"] is None
    pending = deepcopy(source_state(tools)["pending"])
    assert pending["kind"] == "check_attempt"
    assert pending["runs"][0]["started"] is True and count(counter) == "x"
    identities = [pending["attempt_id"], pending["runs"][0]["run_id"]]
    blocked_details(first, "receipt_unavailable", identities=identities)
    second = blocked(handoff(tools, packet), "verification")
    blocked_details(second, "unknown", identities=identities)
    assert source_state(tools)["pending"] == pending
    assert source_state(tools)["claimed_by"] == "handoff-author"
    assert count(counter) == "x"


def test_source_drift_during_incomplete_handoff_never_advances_or_discards_the_change(project, monkeypatch):
    tools, context, packet, counter = start(project)
    original = tools.runtime.advance
    attempts = []

    def temporarily_blocked(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PoiseError("fixture progression temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(tools.runtime, "advance", temporarily_blocked)
    first = blocked(handoff(tools, packet), "progression")
    blocked_details(first, "progression", identities=[packet["input"]["request_id"]])
    assert first["verification"]["status"] == "verified"
    path = Path(context["worktree"]) / "src/double.py"
    path.write_text("def double(n):\n    return n * 3\n", encoding="utf-8")
    second = blocked(handoff(tools, packet), "progression")
    blocked_details(second, "source_drift", identities=["T1"])
    assert second["release"] is None
    state = source_state(tools)
    assert (state["stage"], state["status"], state["claimed_by"]) == (
        "author", "verified", "handoff-author",
    )
    assert path.read_text(encoding="utf-8") == "def double(n):\n    return n * 3\n"
    assert count(counter) == "x"


@pytest.mark.parametrize("checkpoint", ["unknown_gate", "restored_receipt"])
def test_dirty_source_native_commit_preserves_unknown_gate_and_exact_receipt_recovery(project, monkeypatch, checkpoint):
    tools, context, packet, counter = start(project)
    worktree = Path(context["worktree"])
    source = worktree / "src/double.py"
    source.write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    git(worktree, "add", "src/double.py")
    before_head = git(worktree, "rev-parse", "HEAD")
    admitted_tree = git(worktree, "write-tree")
    assert admitted_tree != git(worktree, "rev-parse", "HEAD^{tree}")
    original = tools.runtime.evidence_commands.record_receipt
    captured = []

    def lose_original_receipt(*args, **kwargs):
        captured.append((deepcopy(args), deepcopy(kwargs)))
        raise PoiseError("fixture terminal receipt persistence unavailable")

    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", lose_original_receipt)
    first = blocked(handoff(tools, packet), "verification")
    pending = deepcopy(source_state(tools)["pending"])
    assert pending["kind"] == "check_attempt" and pending["runs"][0]["started"] is True
    assert first["verification"] is None and first["progression"] is None and first["release"] is None
    blocked_details(first, "receipt_unavailable", identities=[pending["attempt_id"], pending["runs"][0]["run_id"]])
    committed_head = git(worktree, "rev-parse", "HEAD")
    assert committed_head != before_head
    assert git(worktree, "rev-parse", "HEAD^{tree}") == admitted_tree
    assert git(worktree, "show", "HEAD:src/double.py") == "def double(n):\n    return n * 2"
    assert tools.runtime.current_task()["publication"] is None
    assert captured and count(counter) == "x"
    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", original)
    second = blocked(handoff(tools, packet), "verification")
    assert source_state(tools)["pending"] == pending and count(counter) == "x"
    assert source_state(tools)["claimed_by"] == "handoff-author"
    assert second["progression"] is None and second["release"] is None
    if checkpoint == "unknown_gate":
        assert "Unknown check outcome" in second["blocked"]["reason"]
        blocked_details(second, "unknown", identities=[pending["attempt_id"], pending["runs"][0]["run_id"]])
        return
    # Restore the exact captured real command receipt through its owner, not a synthetic PASS.
    for args, kwargs in captured:
        original(*args, **kwargs)
    completed = complete(handoff(tools, packet), replayed=False)
    assert completed["verification"]["verification_commit"] == committed_head
    assert completed["verification"]["verified_tree"] == admitted_tree
    state = source_state(tools)
    assert state["pending"] is None and state["claimed_by"] is None
    assert state["status"] == "verified" and state["stage"] == "author"
    assert state["counts"][0] == 1 and count(counter) == "x"
    assert git(worktree, "rev-parse", "HEAD") == committed_head
    complete(handoff(tools, packet), replayed=True)
    assert source_state(tools) == state and count(counter) == "x"


def test_foreign_same_tree_commit_is_not_an_internal_verification_commit(project, monkeypatch):
    tools, context, packet, counter = start(project)
    worktree = Path(context["worktree"])
    (worktree / "src/double.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    git(worktree, "add", "src/double.py")
    admitted_tree = git(worktree, "write-tree")
    assert admitted_tree != git(worktree, "rev-parse", "HEAD^{tree}")
    original = tools.runtime.evidence_commands.record_receipt
    captured = []

    def lose_original_receipt(*args, **kwargs):
        captured.append((deepcopy(args), deepcopy(kwargs)))
        raise PoiseError("fixture terminal receipt persistence unavailable")

    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", lose_original_receipt)
    blocked(handoff(tools, packet), "verification")
    pending = deepcopy(source_state(tools)["pending"])
    own_commit = git(worktree, "rev-parse", "HEAD")
    committed_tree = git(worktree, "rev-parse", "HEAD^{tree}")
    assert committed_tree == admitted_tree
    monkeypatch.setattr(tools.runtime.evidence_commands, "record_receipt", original)
    assert captured
    for args, kwargs in captured:
        original(*args, **kwargs)
    git(worktree, "commit", "--allow-empty", "-m", "Foreign same-tree commit")
    foreign_commit = git(worktree, "rev-parse", "HEAD")
    assert foreign_commit != own_commit and git(worktree, "rev-parse", "HEAD^{tree}") == committed_tree
    result = blocked(handoff(tools, packet), "verification")
    assert "source" in result["blocked"]["reason"].casefold()
    assert "changed" in result["blocked"]["reason"].casefold()
    assert result["verification"] is None and result["progression"] is None and result["release"] is None
    state = source_state(tools)
    assert state["pending"] == pending and state["claimed_by"] == "handoff-author"
    assert state["stage"] == "author" and state["status"] == "active"
    assert git(worktree, "rev-parse", "HEAD") == foreign_commit and count(counter) == "x"
