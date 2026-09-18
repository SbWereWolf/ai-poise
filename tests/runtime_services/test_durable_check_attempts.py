"""A subprocess is never replayed across an uncertain persistence boundary."""
from copy import deepcopy

import pytest

from batch.helpers import result, verify
from poise.common import PoiseError
from runtime_services.test_pending_check_recovery import _valid_scenario


def candidate(project, monkeypatch, *, passing=True):
    tools, context = _valid_scenario(project, passing_continuation=passing)
    payload = result(context, 'Durable check attempt candidate')
    calls = []
    run = tools.runtime.check_runner.run

    def counted(*args, **kwargs):
        calls.append(args[0])
        return run(*args, **kwargs)

    monkeypatch.setattr(tools.runtime.check_runner, 'run', counted)
    return tools, payload, calls


def fail_once(monkeypatch, owner, method):
    original = getattr(owner, method)

    def fail(*args, **kwargs):
        monkeypatch.setattr(owner, method, original)
        raise OSError('injected persistence failure')

    monkeypatch.setattr(owner, method, fail)


@pytest.mark.parametrize('passing', [True, False])
def test_completed_command_reused_after_batch_failure(project, monkeypatch, passing):
    tools, payload, calls = candidate(project, monkeypatch, passing=passing)
    fail_once(monkeypatch, tools.runtime.runner, 'record_observations')
    with pytest.raises(OSError, match='injected persistence'):
        verify(tools, deepcopy(payload))
    pending = tools.runtime.current_task()['pending']
    assert isinstance(pending, dict) and pending['kind'] == 'check_attempt'
    replay = verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert replay['status'] == ('awaiting_continuation' if passing else 'checks_failed')
    assert tools.runtime.current_task()['pending'] is None
    assert tools.runtime.current_task()['attempts'] == 1


def test_started_command_without_receipt_is_unknown_not_retryable(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.evidence_commands, 'record_receipt')
    with pytest.raises(OSError, match='injected persistence'):
        verify(tools, deepcopy(payload))
    before = deepcopy(tools.runtime.current_task())
    with pytest.raises(PoiseError, match='(?i)unknown|неизвестен'):
        verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert tools.runtime.current_task() == before


def test_changed_candidate_cannot_consume_an_unfinished_attempt(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.runner, 'record_observations')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    before = deepcopy(tools.runtime.current_task())
    altered = deepcopy(payload)
    altered['commit_message'] = 'A different request'
    with pytest.raises(PoiseError, match='(?i)exact|точного|changed'):
        verify(tools, altered)
    assert len(calls) == 1
    assert tools.runtime.current_task() == before


def test_tampered_output_does_not_trigger_a_fresh_execution(project, monkeypatch):
    from pathlib import Path
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.runner, 'record_observations')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    saved = tools.runtime.evidence_commands.list_for('T1')
    Path(saved[0]['stdout']).write_text('tampered')
    before = deepcopy(tools.runtime.current_task())
    with pytest.raises(PoiseError, match='(?i)receipt|unknown|неизвестен'):
        verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert tools.runtime.current_task() == before


def test_reserved_unstarted_attempt_resumes_without_new_attempt(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.runner, 'start_check_run')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    before = tools.runtime.current_task()
    assert calls == []
    assert before['pending']['runs'][0]['started'] is False
    replay = verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert calls[0] == before['pending']['runs'][0]['run_id']
    assert replay['status'] == 'awaiting_continuation'
    assert tools.runtime.current_task()['attempts'] == 1


def test_committed_start_permission_without_spawn_remains_unknown(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    original = tools.runtime.runner.start_check_run

    def lost_permission_ack(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(tools.runtime.runner, 'start_check_run', original)
        raise OSError('lost start acknowledgement')

    monkeypatch.setattr(tools.runtime.runner, 'start_check_run', lost_permission_ack)
    with pytest.raises(OSError, match='lost start'):
        verify(tools, deepcopy(payload))
    assert calls == []
    before = deepcopy(tools.runtime.current_task())
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(tools, deepcopy(payload))
    assert calls == []
    assert tools.runtime.current_task() == before


@pytest.mark.parametrize('boundary', ['begin', 'finish'])
def test_execution_save_failure_rolls_back_the_whole_boundary(project, monkeypatch, boundary):
    from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository
    tools, payload, calls = candidate(project, monkeypatch)
    original = SqliteExecutionRepository.save
    failures = []

    def broken_save(self, task_id, data, version):
        current, _ = self.load(task_id)
        beginning = current['pending'] is None and isinstance(data['pending'], dict)
        finishing = isinstance(current['pending'], dict) and data['pending'] is None
        if not failures and ((boundary == 'begin' and beginning) or (boundary == 'finish' and finishing)):
            failures.append(True)
            original(self, task_id, data, version)
            raise OSError('injected boundary rollback')
        return original(self, task_id, data, version)

    monkeypatch.setattr(SqliteExecutionRepository, 'save', broken_save)
    with pytest.raises(OSError, match='boundary rollback'):
        verify(tools, deepcopy(payload))
    state = tools.runtime.current_task()
    assert len(calls) == (0 if boundary == 'begin' else 1)
    assert not any(h.get('event') == 'observations_recorded' for h in state['history'])
    assert state['attempts'] == (0 if boundary == 'begin' else 1)
    assert (state['pending'] is None) == (boundary == 'begin')
    verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert tools.runtime.current_task()['attempts'] == 1


def test_finish_committed_but_response_lost_reuses_batch(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    original = tools.runtime.runner.record_observations

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(tools.runtime.runner, 'record_observations', original)
        raise OSError('lost finish acknowledgement')

    monkeypatch.setattr(tools.runtime.runner, 'record_observations', lost_ack)
    with pytest.raises(OSError, match='lost finish'):
        verify(tools, deepcopy(payload))
    assert tools.runtime.current_task()['pending'] is None
    verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert tools.runtime.current_task()['attempts'] == 1


def test_only_the_unstarted_tail_is_executed(project, monkeypatch):
    from runtime_services import test_failed_check_rework as scenario
    original_bootstrap = scenario.bootstrap

    def two_checks(tools, project):
        task = project['task']
        second = deepcopy(task['methods'][0])
        second['id'] = 'CHECK2'
        second['argv'][-1] = "print('second real check')"
        task['methods'].append(second)
        task['method_inputs'].append({**deepcopy(task['method_inputs'][0]), 'method_id': 'CHECK2'})
        task['checks']['implementation'].append('CHECK2')
        return original_bootstrap(tools, project)

    monkeypatch.setattr(scenario, 'bootstrap', two_checks)
    tools, payload, calls = candidate(project, monkeypatch)
    original = tools.runtime.runner.start_check_run
    starts = []

    def stop_before_second(*args, **kwargs):
        starts.append(args[-1])
        if len(starts) == 2:
            monkeypatch.setattr(tools.runtime.runner, 'start_check_run', original)
            raise OSError('interrupted before tail permission')
        return original(*args, **kwargs)

    monkeypatch.setattr(tools.runtime.runner, 'start_check_run', stop_before_second)
    with pytest.raises(OSError, match='tail permission'):
        verify(tools, deepcopy(payload))
    runs = tools.runtime.current_task()['pending']['runs']
    assert [r['started'] for r in runs] == [True, False]
    assert len(calls) == 1
    replay = verify(tools, deepcopy(payload))
    assert calls == [r['run_id'] for r in runs]
    assert [r['id'] for r in replay['checks']] == calls


def test_stale_start_permission_is_single_use(project, monkeypatch):
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.runner, 'start_check_run')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    pending = tools.runtime.current_task()['pending']
    run_id = pending['runs'][0]['run_id']
    tools.runtime.runner.start_check_run('T1', tools.runtime.session, deepcopy(pending), run_id)
    with pytest.raises(PoiseError, match='changed before run permission'):
        tools.runtime.runner.start_check_run('T1', tools.runtime.session, deepcopy(pending), run_id)
    assert calls == []


@pytest.mark.parametrize('drift', ['protocol', 'method', 'tree', 'owner'])
def test_attempt_replay_rejects_drift_without_a_new_effect(project, monkeypatch, drift):
    from pathlib import Path
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.runner, 'record_observations')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    current = tools.runtime.current_task()
    if drift in {'protocol', 'method'}:
        pending = deepcopy(current['pending'])
        if drift == 'protocol':
            pending['version'] = 999
        else:
            pending['runs'][0]['method_id'] = 'OTHER'
        with tools.runtime.task_commands.unit_of_work() as uow:
            uow.execution.patch('T1', {'pending': pending})
    elif drift == 'tree':
        Path(current['worktree'], 'src', 'another.py').write_text('changed = True\n')
    else:
        with pytest.raises(PoiseError):
            tools.runtime.runner.current_check_attempt('T1', 'foreign-actor',
                current['pending']['verified_tree'], current['pending']['execution_key'], ['CHECK'])
        assert len(calls) == 1
        assert tools.runtime.current_task() == current
        return
    before = deepcopy(tools.runtime.current_task())
    with pytest.raises(PoiseError):
        verify(tools, deepcopy(payload))
    assert len(calls) == 1
    assert tools.runtime.current_task() == before
