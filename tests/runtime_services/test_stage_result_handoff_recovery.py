"""Integration subject: exact continuation across committed real-owner effects."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
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
