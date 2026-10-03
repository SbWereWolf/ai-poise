"""A missing terminal receipt needs an owned, independently approved recovery path."""

from copy import deepcopy
import sys
import threading
import time

import pytest

from batch.helpers import request, verify
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_durable_check_attempts import candidate, fail_once
from runtime_services.test_task_restart import restart
from runtime_services.test_failed_check_rework import _scenario
from batch.helpers import result


def _pending(project, monkeypatch):
    project['task']['planning'] = {
        'schema': 'task-planning-1', 'template': None,
        'restart_revision_policy': {'reviewer': ['process'], 'user': ['process']},
    }
    producer, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, producer.runtime.evidence_commands, 'record_receipt')
    with pytest.raises(OSError, match='injected persistence failure'):
        verify(producer, deepcopy(payload))
    pending = deepcopy(producer.runtime.current_task()['pending'])
    assert pending['kind'] == 'check_attempt'
    assert pending['runs'][0]['started'] is True
    assert producer.runtime.evidence_commands.list_for('T1') == []
    assert calls == [pending['runs'][0]['run_id']]
    return producer, payload, pending, calls


def _handoff(producer, pending, *, request_id='uncertain-transfer', attempt_id=None):
    return producer.invoke(request('handoff', {
        'request_id': request_id,
        'reason': 'Transfer the exact uncertain check to an independent reviewer.',
        'result': None,
        'commit_message': None,
        'artifact_paths': [],
        'uncertain_check_recovery': {
            'attempt_id': pending['attempt_id'] if attempt_id is None else attempt_id,
            'quiescence_evidence': 'The originating runner returned; no owned run remains active.',
            'effects_evidence': 'The disposable check had no external side effect.',
        },
    }))


def test_stopped_missing_receipt_transfers_for_distinct_reviewer_restart(
    project, monkeypatch,
):
    producer, payload, pending, calls = _pending(project, monkeypatch)
    original = deepcopy(producer.runtime.current_task())
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(producer, deepcopy(payload))
    assert producer.runtime.current_task() == original
    transfer = _handoff(producer, pending)
    assert transfer['status'] == 'handed_off'
    assert producer.runtime.ownership.snapshot(producer.runtime.session).task_id is None

    reviewer = WorkTools(WorkPoise(project['config_path'], 'independent-check-reviewer'))
    resumed = reviewer.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert resumed['status'] == 'active'
    before = deepcopy(reviewer.runtime.current_task())
    assert before['pending'] == pending
    restarted = restart(reviewer, 'T1', before['version'], 'reviewed-uncertain-restart',
        authorization={
            'role': 'reviewer',
            'decision': 'I inspected the original run, absence of terminal receipt, '
                        'quiescence and external effects; archive without replay.',
        })
    assert restarted['status'] == 'newborn'
    after = reviewer.runtime.task_queries.record('T1')
    assert after['pending'] is None
    assert after['restart_history'][-1]['abandoned_check_attempt'] == pending
    assert after['history'][:len(original['history'])] == original['history']
    assert after['worktree'] == original['worktree']
    assert after['branch'] == original['branch']
    assert calls == [pending['runs'][0]['run_id']]
    replay = restart(reviewer, 'T1', before['version'], 'reviewed-uncertain-restart',
        authorization={
            'role': 'reviewer',
            'decision': 'I inspected the original run, absence of terminal receipt, '
                        'quiescence and external effects; archive without replay.',
        })
    assert replay['replayed'] is True
    assert reviewer.runtime.task_queries.record('T1') == after


def test_wrong_attempt_identity_refuses_recovery_transfer_without_effect(
    project, monkeypatch,
):
    producer, _, pending, calls = _pending(project, monkeypatch)
    before = deepcopy(producer.runtime.current_task())
    with pytest.raises(PoiseError, match='attempt|identity|mismatch'):
        _handoff(producer, pending, request_id='wrong-attempt', attempt_id='another-attempt')
    assert producer.runtime.current_task() == before
    assert producer.runtime.ownership.snapshot(producer.runtime.session).task_id == 'T1'
    assert calls == [pending['runs'][0]['run_id']]


def test_live_owned_run_refuses_transfer_even_from_fresh_runtime_instance(
    project,
):
    policy = project['cfg']['runtime_services']['check_runner']
    policy.update(initial_seconds=15, progress_gap_seconds=15, poll_seconds=0.02)
    producer, context = _scenario(
        project,
        command=[sys.executable, '-S', '-c', 'import time; time.sleep(10)'],
    )
    outcome = {}

    def live_run():
        try:
            outcome['result'] = verify(producer, result(context, 'Active check.'))
        except Exception as exc:
            outcome['error'] = exc

    thread = threading.Thread(target=live_run)
    thread.start()
    run_id = None
    try:
        deadline = time.monotonic() + 5
        while not producer.runtime.check_runner.active_ids() and time.monotonic() < deadline:
            time.sleep(0.01)
        active = producer.runtime.check_runner.active_ids()
        assert len(active) == 1
        run_id = active[0]
        assert run_id in producer.runtime.check_runner.active_ids()
        fresh = WorkTools(WorkPoise(project['config_path'], producer.runtime.session))
        assert fresh.runtime.check_runner.active_ids() == ()
        before = deepcopy(fresh.runtime.current_task())
        pending = before['pending']
        assert pending['runs'][0]['run_id'] == run_id
        assert pending['runs'][0]['started'] is True
        with pytest.raises(PoiseError, match='running|active|quiescence'):
            _handoff(fresh, pending, request_id='refuse-live-transfer')
        assert fresh.runtime.current_task() == before
        assert fresh.runtime.ownership.snapshot(fresh.runtime.session).task_id == 'T1'
    finally:
        if run_id is not None:
            producer.runtime.check_runner.cancel(run_id)
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome
