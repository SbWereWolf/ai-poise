"""Restart archives historical checks; it never grants replay after ownership drift."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import verify
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_durable_check_attempts import candidate, fail_once
from runtime_services.test_task_restart import restart


def uncertain(project, monkeypatch):
    project['task']['planning'] = {
        'schema': 'task-planning-1', 'template': None,
        'restart_revision_policy': {'reviewer': ['process'], 'user': ['process']},
    }
    producer, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, producer.runtime.evidence_commands, 'record_receipt')
    with pytest.raises(OSError, match='injected persistence failure'):
        verify(producer, deepcopy(payload))
    original = deepcopy(producer.runtime.current_task())
    assert original['pending']['kind'] == 'check_attempt'
    assert len(calls) == 1
    producer.runtime.ownership.release_task('T1')
    receiver = WorkTools(WorkPoise(project['config_path'], 'restart-reviewer'))
    assert receiver.runtime.bootstrap({'id': 'T1'})['status'] == 'active'
    return producer, receiver, original, calls


def authorised_restart(receiver, version, request_id='archive-after-ownership'):
    return restart(receiver, 'T1', version, request_id, authorization={
        'role': 'reviewer',
        'decision': 'Archive the stopped attempt; preserve evidence and worktree without replay.',
    })


def test_reviewer_restart_archives_original_attempt_after_ownership_transfer(project, monkeypatch):
    producer, receiver, original, calls = uncertain(project, monkeypatch)
    before = deepcopy(receiver.runtime.current_task())
    worktree = Path(before['worktree'])
    (worktree / 'reviewer-recovery.txt').write_text('Keep existing work\n')
    with pytest.raises(PoiseError, match='context changed'):
        receiver.runtime.runner.current_check_attempt(
            'T1', receiver.runtime.session, original['pending']['verified_tree'],
            original['pending']['execution_key'], ['CHECK'],
        )
    newborn = authorised_restart(receiver, before['version'])
    assert newborn['status'] == 'newborn'
    assert newborn['task'] == 'T1'
    assert newborn['claimed_by'] == 'restart-reviewer'
    after = receiver.runtime.task_queries.record('T1')
    assert after['pending'] is None
    assert after['attempts'] == 0
    assert after['worktree'] == before['worktree']
    assert after['branch'] == before['branch']
    assert after['restart_history'][-1]['abandoned_check_attempt'] == original['pending']
    assert after['history'][:len(before['history'])] == before['history']
    assert (worktree / 'reviewer-recovery.txt').read_text() == 'Keep existing work\n'
    assert calls == [original['pending']['runs'][0]['run_id']]
    assert producer.runtime.ownership.snapshot(producer.runtime.session).task_id is None
    replay = authorised_restart(receiver, before['version'])
    assert replay['replayed'] is True
    assert receiver.runtime.task_queries.record('T1') == after
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_same_owner_restart_after_release_and_reacquire(project, monkeypatch):
    producer, receiver, original, calls = uncertain(project, monkeypatch)
    receiver.runtime.ownership.release_task('T1')
    assert producer.runtime.bootstrap({'id': 'T1'})['status'] == 'active'
    before = producer.runtime.current_task()
    assert restart(producer, 'T1', before['version'], authorization={
        'role': 'user', 'decision': 'Explicit user authorizes this stopped-attempt recovery.',
    })['status'] == 'newborn'
    after = producer.runtime.task_queries.record('T1')
    assert after['restart_history'][-1]['abandoned_check_attempt'] == original['pending']
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_released_task_restart_does_not_require_a_new_execution_claim(project, monkeypatch):
    _, receiver, original, calls = uncertain(project, monkeypatch)
    receiver.runtime.ownership.release_task('T1')
    before = receiver.runtime.task_queries.record('T1')
    assert authorised_restart(receiver, before['version'])['status'] == 'newborn'
    after = receiver.runtime.task_queries.record('T1')
    assert after['restart_history'][-1]['abandoned_check_attempt'] == original['pending']
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_reviewer_archival_still_rejects_a_running_check(project, monkeypatch):
    _, receiver, original, calls = uncertain(project, monkeypatch)
    before = deepcopy(receiver.runtime.current_task())
    monkeypatch.setattr(receiver.runtime.check_runner, 'active_ids',
                        lambda: (original['pending']['runs'][0]['run_id'],))
    with pytest.raises(PoiseError, match='still running'):
        authorised_restart(receiver, before['version'])
    assert receiver.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_executor_cannot_authorise_its_own_review_restart(project, monkeypatch):
    producer, receiver, original, calls = uncertain(project, monkeypatch)
    receiver.runtime.ownership.release_task('T1')
    producer.runtime.bootstrap({'id': 'T1'})
    before = deepcopy(producer.runtime.current_task())
    with pytest.raises(PoiseError, match='distinct reviewer'):
        authorised_restart(producer, before['version'])
    assert producer.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_foreign_current_owner_prevents_archival(project, monkeypatch):
    producer, receiver, original, calls = uncertain(project, monkeypatch)
    receiver.runtime.ownership.release_task('T1')
    producer.runtime.bootstrap({'id': 'T1'})
    before = deepcopy(producer.runtime.current_task())
    with pytest.raises(PoiseError, match='owned by another session'):
        authorised_restart(receiver, before['version'])
    assert producer.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


@pytest.mark.parametrize('event', ['submitted', 'missing-version'])
def test_archival_rejects_nonownership_or_incomplete_history(project, monkeypatch, event):
    _, receiver, original, calls = uncertain(project, monkeypatch)
    current = receiver.runtime.current_task()
    # Fault injection is confined to the disposable fixture DB, never the working Task DB.
    with receiver.runtime.store.transaction() as database:
        if event == 'submitted':
            database.execute(
                "UPDATE task_events SET data=json_set(data,'$.event','submitted') "
                "WHERE task_id=? AND version=?", ('T1', current['version']),
            )
        else:
            database.execute(
                'UPDATE task_events SET version=? WHERE task_id=? AND version=?',
                (current['version'] + 100, 'T1', current['version']),
            )
    before = deepcopy(receiver.runtime.current_task())
    with pytest.raises(PoiseError, match='context changed'):
        authorised_restart(receiver, before['version'])
    assert receiver.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


@pytest.mark.parametrize(('field', 'value'), [
    ('version', 999), ('task_version', True), ('task_version', -1),
    ('task_version', 999999), ('actor', ''), ('stage', 'wrong-stage'),
    ('iteration', 99), ('submission_digest', 'different-candidate'),
    ('attempt_id', 'not-a-uuid'),
])
def test_archival_rejects_damaged_attempt_without_mutation(project, monkeypatch, field, value):
    _, receiver, original, calls = uncertain(project, monkeypatch)
    pending = deepcopy(original['pending'])
    pending[field] = value
    with receiver.runtime.task_commands.unit_of_work() as unit:
        unit.execution.patch('T1', {'pending': pending})
    before = deepcopy(receiver.runtime.current_task())
    with pytest.raises(PoiseError):
        authorised_restart(receiver, before['version'])
    assert receiver.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


@pytest.mark.parametrize('field', ['verified_tree', 'execution_key', 'actor', 'runs'])
def test_archival_rejects_missing_attempt_fields(project, monkeypatch, field):
    _, receiver, original, calls = uncertain(project, monkeypatch)
    pending = deepcopy(original['pending'])
    del pending[field]
    with receiver.runtime.task_commands.unit_of_work() as unit:
        unit.execution.patch('T1', {'pending': pending})
    before = deepcopy(receiver.runtime.current_task())
    with pytest.raises(PoiseError):
        authorised_restart(receiver, before['version'])
    assert receiver.runtime.current_task() == before
    assert calls == [original['pending']['runs'][0]['run_id']]


def test_archival_after_transfer_rolls_back_with_failed_restart(project, monkeypatch):
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository

    _, receiver, original, calls = uncertain(project, monkeypatch)
    before = deepcopy(receiver.runtime.current_task())
    ownership_before = receiver.runtime.ownership.snapshot(receiver.runtime.session)
    assert ownership_before.task_id == 'T1'
    assert ownership_before.worktree_task_id == 'T1'
    fail_once(monkeypatch, SqliteTaskRepository, 'restart_newborn')
    with pytest.raises(OSError, match='injected persistence failure'):
        authorised_restart(receiver, before['version'])
    assert receiver.runtime.current_task() == before
    assert receiver.runtime.ownership.snapshot(receiver.runtime.session) == ownership_before
    assert calls == [original['pending']['runs'][0]['run_id']]
    assert authorised_restart(receiver, before['version'])['status'] == 'newborn'
