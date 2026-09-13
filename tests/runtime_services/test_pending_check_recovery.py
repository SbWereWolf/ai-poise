"""Recovery of a terminal observation batch left behind pending=checks."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import result, verify
from poise.common import PoiseError
from runtime_services.test_failed_check_rework import _scenario, _show


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


def test_exact_verify_replay_recovers_complete_failed_batch_without_rerun(project):
    tools, context = _scenario(project)
    payload = result(context, "failed candidate")
    failed = verify(tools, deepcopy(payload))
    assert failed["status"] == "checks_failed"
    before = _show(tools)
    observations = _show(tools, "evidence")["observations"]
    _mark_pending(tools)

    recovered = verify(tools, deepcopy(payload))

    after = _show(tools)
    assert recovered["status"] == "checks_failed"
    assert recovered["checks"] == failed["checks"]
    assert after["attempts"] == before["attempts"]
    assert after["submission_count"] == before["submission_count"]
    assert after["evidence_count"] == before["evidence_count"]
    assert _show(tools, "evidence")["observations"] == observations
    assert tools.runtime.current_task()["pending"] is None
    assert len(_recovery_events(tools)) == 1

    replay = verify(tools, deepcopy(payload))
    assert replay["status"] == "checks_failed"
    assert replay["checks"] == failed["checks"]
    assert len(_recovery_events(tools)) == 1


def test_exact_verify_replay_recovers_complete_passing_batch_into_evidence_flow(project):
    tools, context = _scenario(project, passing_continuation=True)
    payload = result(context, "passing observation awaiting evidence")
    awaiting = verify(tools, deepcopy(payload))
    assert awaiting["status"] == "awaiting_continuation"
    before = _show(tools)
    _mark_pending(tools)

    recovered = verify(tools, deepcopy(payload))

    after = _show(tools)
    assert recovered["status"] == "awaiting_continuation"
    assert recovered["checks"] == awaiting["checks"]
    assert after["attempts"] == before["attempts"]
    assert after["submission_count"] == before["submission_count"]
    assert after["evidence_count"] == before["evidence_count"]
    assert tools.runtime.current_task()["pending"] is None
    assert len(_recovery_events(tools)) == 1


def test_pending_checks_without_complete_batch_is_rejected_without_mutation(project):
    tools, context = _scenario(project)
    payload = result(context, "submitted without observations")
    tools.runtime.task_commands.submit("T1", tools.runtime.session, deepcopy(payload))
    _mark_pending(tools)
    before = deepcopy(tools.runtime.current_task())
    history = deepcopy(_show(tools)["history"])

    with pytest.raises(PoiseError, match="Неизвестен исход прерванной проверки"):
        verify(tools, deepcopy(payload))

    assert tools.runtime.current_task() == before
    assert _show(tools)["history"] == history


def test_pending_checks_rejects_a_different_submission_without_mutation(project):
    tools, context = _scenario(project)
    payload = result(context, "original failed candidate")
    assert verify(tools, deepcopy(payload))["status"] == "checks_failed"
    _mark_pending(tools)
    before = deepcopy(tools.runtime.current_task())
    history = deepcopy(_show(tools)["history"])

    changed = result(context, "different candidate")
    with pytest.raises(PoiseError, match="текущего submitted результата"):
        verify(tools, changed)

    assert tools.runtime.current_task() == before
    assert _show(tools)["history"] == history


def test_pending_checks_rejects_corrupt_receipt_output_without_mutation(project):
    tools, context = _scenario(project)
    payload = result(context, "failed candidate")
    failed = verify(tools, deepcopy(payload))
    assert failed["status"] == "checks_failed"
    Path(failed["checks"][0]["stdout"]).write_text("corrupt\n", encoding="utf-8")
    _mark_pending(tools)
    before = deepcopy(tools.runtime.current_task())
    history = deepcopy(_show(tools)["history"])

    with pytest.raises(PoiseError, match="Неизвестен исход прерванной проверки"):
        verify(tools, deepcopy(payload))

    assert tools.runtime.current_task() == before
    assert _show(tools)["history"] == history
