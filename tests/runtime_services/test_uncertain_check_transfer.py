"""A missing terminal receipt needs an owned, independently approved recovery path."""

from copy import deepcopy
import multiprocessing
from pathlib import Path
import sys
import threading
import time

import pytest

from batch.helpers import request, verify
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_durable_check_attempts import candidate, fail_once
from runtime_services.test_task_restart import restart
from runtime_services.test_failed_check_rework import _scenario
from batch.helpers import result
from poise.infrastructure.sqlite.ownership import SqliteOwnershipRepository


def _pending(project, monkeypatch):
    project['task']['planning'] = {
        'schema': 'task-planning-1', 'template': None,
        'restart_revision_policy': {'reviewer': ['workflow'], 'user': ['workflow']},
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
    ownership = producer.runtime.ownership.snapshot(producer.runtime.session)
    assert ownership.task_id == 'T1'
    assert ownership.worktree_task_id == 'T1'
    return producer, payload, pending, calls


def _release(producer, *, request_id='uncertain-release', version=None):
    current = producer.runtime.current_task()
    return producer.invoke(request('release', {
        'request_id': request_id,
        'task_id': current['id'],
        'expected_version': current['version'] if version is None else version,
        'reason': 'An independent participant inspects the unchanged uncertain check in place.',
    }))


def test_stopped_missing_receipt_is_released_for_distinct_reviewer_restart(
    project, monkeypatch,
):
    producer, payload, pending, calls = _pending(project, monkeypatch)
    original = deepcopy(producer.runtime.current_task())
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(producer, deepcopy(payload))
    assert producer.runtime.current_task() == original
    released_result = _release(producer)
    assert released_result['status'] == 'ownership_released'
    released = producer.runtime.ownership.snapshot(producer.runtime.session)
    assert released.task_id is None
    assert released.worktree_task_id is None

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


def test_stale_release_version_preserves_the_exact_unknown_attempt(
    project, monkeypatch,
):
    producer, _, pending, calls = _pending(project, monkeypatch)
    before = deepcopy(producer.runtime.current_task())
    ownership_before = producer.runtime.ownership.snapshot(producer.runtime.session)
    with pytest.raises(PoiseError, match='Release version conflict'):
        _release(producer, request_id='stale-release', version=before['version'] - 1)
    assert producer.runtime.current_task() == before
    assert producer.runtime.ownership.snapshot(producer.runtime.session) == ownership_before
    assert calls == [pending['runs'][0]['run_id']]


def _verify_in_other_process(config_path, session, payload, ready, stop, outcome):
    producer = WorkTools(WorkPoise(config_path, session))

    def observe_and_cancel():
        deadline = time.monotonic() + 5
        while not producer.runtime.check_runner.active_ids() and time.monotonic() < deadline:
            time.sleep(0.01)
        active = producer.runtime.check_runner.active_ids()
        ready.put(active)
        stop.wait(10)
        for run_id in active:
            producer.runtime.check_runner.cancel(run_id)

    observer = threading.Thread(target=observe_and_cancel)
    observer.start()
    try:
        outcome.put(verify(producer, deepcopy(payload)))
    except Exception as exc:
        outcome.put(repr(exc))
    finally:
        stop.set()
        observer.join(timeout=5)


def test_release_preserves_a_live_run_and_next_actor_cannot_reexecute_it(project):
    policy = project['cfg']['runtime_services']['check_runner']
    policy.update(initial_seconds=15, progress_gap_seconds=15, poll_seconds=0.02)
    producer, context = _scenario(
        project,
        command=[sys.executable, '-S', '-c', 'import time; time.sleep(10)'],
    )
    session = producer.runtime.session
    payload = result(context, 'Active check.')
    process_context = multiprocessing.get_context('spawn')
    ready = process_context.Queue()
    stop = process_context.Event()
    outcome = process_context.Queue()
    child = process_context.Process(
        target=_verify_in_other_process,
        args=(project['config_path'], session, payload, ready, stop, outcome),
    )
    child.start()
    try:
        active = ready.get(timeout=10)
        assert len(active) == 1
        assert child.is_alive()
        fresh = WorkTools(WorkPoise(project['config_path'], session))
        assert fresh.runtime.check_runner.active_ids() == ()
        before = deepcopy(fresh.runtime.current_task())
        ownership_before = fresh.runtime.ownership.snapshot(session)
        assert ownership_before.task_id == 'T1'
        assert ownership_before.worktree_task_id == 'T1'
        pending = before['pending']
        assert pending['runs'][0]['run_id'] == active[0]
        assert pending['runs'][0]['started'] is True
        assert _release(fresh, request_id='release-live-run')['status'] == 'ownership_released'
        after = fresh.runtime.task_queries.record('T1')
        assert after['pending'] == pending
        assert after['claimed_by'] is None
        assert child.is_alive()
        reviewer = WorkTools(WorkPoise(project['config_path'], 'live-check-reviewer'))
        acquired = reviewer.invoke(request('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
        assert acquired['status'] == 'active'
        assert reviewer.runtime.current_task()['pending'] == pending
        unresolved = deepcopy(reviewer.runtime.current_task())
        assert pending['actor'] == session
        assert pending['actor'] != reviewer.runtime.session
        with pytest.raises(PoiseError, match='Check attempt context changed'):
            verify(reviewer, deepcopy(payload))
        assert reviewer.runtime.current_task() == unresolved
        assert reviewer.runtime.check_runner.active_ids() == ()
        assert child.is_alive()
    finally:
        stop.set()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
    assert child.exitcode == 0
    assert not outcome.empty()


def test_uncertain_release_failure_rolls_back_without_losing_work_or_pending_state(
    project, monkeypatch,
):
    producer, _, pending, calls = _pending(project, monkeypatch)
    before = deepcopy(producer.runtime.current_task())
    worktree = Path(before['worktree'])
    source = worktree / 'src' / 'recovery-wip.txt'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('preserve source bytes across failed release\n')
    original_bytes = source.read_bytes()
    original_head = git(worktree, 'rev-parse', 'HEAD')
    original_status = git(worktree, 'status', '--short')
    ownership_before = producer.runtime.ownership.snapshot(producer.runtime.session)
    assert ownership_before.task_id == 'T1'
    assert ownership_before.worktree_task_id == 'T1'
    original_record = SqliteOwnershipRepository.record_ordinary_release

    def fail_release(self, intent, actor, release_result):
        original_record(self, intent, actor, release_result)
        raise OSError('injected release rollback')

    monkeypatch.setattr(SqliteOwnershipRepository, 'record_ordinary_release', fail_release)
    with pytest.raises(OSError, match='injected release rollback'):
        _release(producer, request_id='release-rollback')
    assert producer.runtime.current_task() == before
    assert producer.runtime.ownership.snapshot(producer.runtime.session) == ownership_before
    assert source.read_bytes() == original_bytes
    assert git(worktree, 'rev-parse', 'HEAD') == original_head
    assert git(worktree, 'status', '--short') == original_status
    assert producer.runtime.evidence_commands.list_for('T1') == []
    with producer.runtime.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM journal WHERE event='ownership.ordinary_released'").fetchone()[0] == 0
    monkeypatch.setattr(SqliteOwnershipRepository, 'record_ordinary_release', original_record)
    released_result = _release(producer, request_id='release-rollback')
    assert released_result['status'] == 'ownership_released'
    assert producer.runtime.current_task() is None
    released = producer.runtime.ownership.snapshot(producer.runtime.session)
    assert released.task_id is None
    assert released.worktree_task_id is None
    assert source.read_bytes() == original_bytes
    assert calls == [pending['runs'][0]['run_id']]
