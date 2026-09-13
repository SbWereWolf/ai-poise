"""Recovery of a terminal observation batch left behind pending=checks."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import result, verify
from poise.common import PoiseError
from runtime_services.test_failed_check_rework import _scenario, _show


def _valid_scenario(project, **options):
    project["task"]["stage_contracts"] = [
        {
            "stage_id": "implementation",
            "allowed_paths": ["src/**"],
            "entry_requirements": [],
            "exit_requirements": [],
        },
        {
            "stage_id": "test_remediation",
            "allowed_paths": ["tests/**"],
            "entry_requirements": [],
            "exit_requirements": [],
        },
    ]
    return _scenario(project, **options)


def _mark_pending(tools):
    current = tools.runtime.current_task()
    current["pending"] = "checks"
    tools.runtime.store.save(current)


def _recovery_events(tools):
    return [
        event
        for event in _show(tools)["history"]
        if event.get("event") == "pending_checks_recovered"
    ]


def _forbid_check_execution(tools, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("recovery reran an external check")

    monkeypatch.setattr(tools.runtime, "_execute_checks", unexpected)


def _snapshot(tools):
    return {
        "task": deepcopy(tools.runtime.current_task()),
        "public_task": deepcopy(_show(tools)),
        "evidence": deepcopy(_show(tools, "evidence")),
    }


def _assert_only_recovery_delta(before, after):
    before_task = deepcopy(before["task"])
    after_task = deepcopy(after["task"])
    assert before_task.pop("pending") == "checks"
    assert after_task.pop("pending") is None
    assert after_task.pop("version") == before_task.pop("version") + 1
    assert after_task == before_task

    before_public = deepcopy(before["public_task"])
    after_public = deepcopy(after["public_task"])
    before_history = before_public.pop("history")
    after_history = after_public.pop("history")
    assert after_public == before_public
    assert after_history[:-1] == before_history
    assert after_history[-1] == {
        "event": "pending_checks_recovered",
        "iteration": before["task"]["iteration"],
        "reason": None,
        "stage": before["task"]["process"]["stages"][before["task"]["stage_index"]]["id"],
        "submission": None,
    }
    assert after["evidence"] == before["evidence"]


def _mutate_latest_batch(tools, mutation):
    with tools.runtime.store.transaction() as database:
        row = database.execute(
            "SELECT data FROM task_proofs WHERE task_id='T1'"
        ).fetchone()
        proof = json.loads(row[0])
        mutation(proof["book"]["batches"][-1]["receipts"])
        database.execute(
            "UPDATE task_proofs SET data=? WHERE task_id='T1'",
            (
                json.dumps(
                    proof,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )


def test_exact_verify_replay_recovers_complete_failed_batch_without_rerun(
    project, monkeypatch
):
    tools, context = _valid_scenario(project)
    payload = result(context, "failed candidate")
    failed = verify(tools, deepcopy(payload))
    assert failed["status"] == "checks_failed"
    _mark_pending(tools)
    before = _snapshot(tools)
    _forbid_check_execution(tools, monkeypatch)

    try:
        recovered = verify(tools, deepcopy(payload))
    except PoiseError as error:
        assert "Неизвестен исход прерванной проверки" in str(error)
        pytest.fail("RECOVERY_NOT_IMPLEMENTED_FAILED_BATCH", pytrace=False)

    assert recovered["status"] == "checks_failed"
    assert recovered["checks"] == failed["checks"]
    assert tools.runtime.current_task()["pending"] is None
    assert len(_recovery_events(tools)) == 1
    recovered_state = _snapshot(tools)
    _assert_only_recovery_delta(before, recovered_state)

    replay = verify(tools, deepcopy(payload))
    assert replay == recovered
    assert _snapshot(tools) == recovered_state


def test_exact_verify_replay_recovers_complete_passing_batch_into_evidence_flow(
    project, monkeypatch
):
    tools, context = _valid_scenario(project, passing_continuation=True)
    payload = result(context, "passing observation awaiting evidence")
    awaiting = verify(tools, deepcopy(payload))
    assert awaiting["status"] == "awaiting_continuation"
    _mark_pending(tools)
    before = _snapshot(tools)
    _forbid_check_execution(tools, monkeypatch)

    try:
        recovered = verify(tools, deepcopy(payload))
    except PoiseError as error:
        assert "Неизвестен исход прерванной проверки" in str(error)
        pytest.fail("RECOVERY_NOT_IMPLEMENTED_PASSING_BATCH", pytrace=False)

    assert recovered["status"] == "awaiting_continuation"
    assert recovered["checks"] == awaiting["checks"]
    assert tools.runtime.current_task()["pending"] is None
    assert len(_recovery_events(tools)) == 1
    recovered_state = _snapshot(tools)
    _assert_only_recovery_delta(before, recovered_state)

    replay = verify(tools, deepcopy(payload))
    assert replay == recovered
    assert _snapshot(tools) == recovered_state


def test_pending_checks_without_complete_batch_is_rejected_without_mutation(project):
    tools, context = _valid_scenario(project)
    payload = result(context, "submitted without observations")
    tools.runtime.task_commands.submit("T1", tools.runtime.session, deepcopy(payload))
    _mark_pending(tools)
    before = _snapshot(tools)

    with pytest.raises(PoiseError, match="Неизвестен исход прерванной проверки"):
        verify(tools, deepcopy(payload))

    assert _snapshot(tools) == before


def test_pending_checks_rejects_a_different_submission_without_mutation(project):
    tools, context = _valid_scenario(project)
    payload = result(context, "original failed candidate")
    assert verify(tools, deepcopy(payload))["status"] == "checks_failed"
    _mark_pending(tools)
    before = _snapshot(tools)

    changed = result(context, "different candidate")
    with pytest.raises(
        PoiseError,
        match="прерванной проверки|текущего submitted результата",
    ):
        verify(tools, changed)

    assert _snapshot(tools) == before


@pytest.mark.parametrize("corruption", ("duplicate", "provenance", "output"))
def test_pending_checks_rejects_ambiguous_or_corrupt_receipts_without_mutation(
    project, corruption
):
    tools, context = _valid_scenario(project)
    payload = result(context, "failed candidate")
    failed = verify(tools, deepcopy(payload))
    assert failed["status"] == "checks_failed"
    if corruption == "duplicate":
        _mutate_latest_batch(tools, lambda receipts: receipts.append(deepcopy(receipts[0])))
    elif corruption == "provenance":
        _mutate_latest_batch(
            tools,
            lambda receipts: receipts[0]["source_provenance"]["bindings"][0].update(
                resolved_path="/different/worktree"
            ),
        )
    else:
        Path(failed["checks"][0]["stdout"]).write_text("corrupt\n", encoding="utf-8")
    _mark_pending(tools)
    before = _snapshot(tools)

    with pytest.raises(PoiseError, match="Неизвестен исход прерванной проверки"):
        verify(tools, deepcopy(payload))

    assert _snapshot(tools) == before
