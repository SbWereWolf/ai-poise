"""Preserved integration history is readable without becoming current proof."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sqlite3

import pytest

from conftest import git
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import integration_input, prepare_completed_task, request, source_change
from .test_current_registry_publication import current_method


HISTORIES = json.loads(Path(__file__).with_name('fixtures').joinpath(
    'history_without_batch.json').read_text())


def historical_entries(history, shape):
    """Test-owned input: genuine old writer shape, or missing identity metadata."""
    return [deepcopy(HISTORIES[shape]) if item.get('event') == 'checks_passed'
            else deepcopy(item) for item in history]


def database(project):
    return project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']


def execution(project):
    with sqlite3.connect(database(project)) as connection:
        return connection.execute(
            'SELECT version,data FROM task_execution WHERE task_id=?', ('T1',),
        ).fetchone()


def seed_historical_input(project, shape):
    # Only this disposable test DB; never a working Task or native receipt.
    version, raw = execution(project)
    data = json.loads(raw)
    history = historical_entries(data['pending']['history'], shape)
    data['pending']['history'] = history
    with sqlite3.connect(database(project)) as connection:
        connection.execute('UPDATE task_execution SET data=? WHERE task_id=? AND version=?',
                           (json.dumps(data), 'T1', version))
    return history


def prepare(project):
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[current_method()], checks=['CURRENT_RESULT'],
    )
    return tools, worktree, request('integrate', integration_input(project, accepted))


def block_publication(self, run, repository):
    blocked = run.publication_blocked('test_publication_blocked', {})
    self._save(run.intent.task_id, blocked, run.version)
    return blocked


@pytest.mark.parametrize('shape', ['absent_details', 'absent_check_ids', 'modern'])
@pytest.mark.parametrize('route', ['blocked_query', 'terminal_query', 'terminal_replay'])
def test_public_history_read_preserves_state_without_executing_checks(
    project, monkeypatch, shape, route,
):
    tools, worktree, packet = prepare(project)
    with monkeypatch.context() as patch:
        if route == 'blocked_query':
            patch.setattr(RuntimeResultIntegration, '_publish', block_publication)
        first = tools.invoke(packet)
    history = first['history'] if shape == 'modern' else seed_historical_input(project, shape)
    before = execution(project)
    target = git(project['app'], 'rev-parse', 'HEAD')

    def forbidden_checks(*args, **kwargs):
        pytest.fail('Read/replay must not execute checks')

    monkeypatch.setattr(RuntimeResultIntegration, '_run_checks', forbidden_checks)
    if route == 'terminal_replay':
        result = tools.invoke(packet)
        assert result['replayed'] is True
    else:
        result = tools.invoke(request('show', {'queries': [{
            'id': 'history', 'kind': 'integration', 'task_id': 'T1', 'request_id': 'integrate-1',
        }]}))['results'][0]['value']
        assert execution(project) == before
    assert result['phase'] == ('publication_failed' if route == 'blocked_query' else 'integrated')
    assert result['history'] == history
    assert result['checks'] == first['checks']
    assert 'acceptance_manifests' not in result
    assert git(project['app'], 'rev-parse', 'HEAD') == target
    assert worktree.exists() is (route == 'blocked_query')


@pytest.mark.parametrize('shape', ['absent_details', 'absent_check_ids'])
def test_blocked_return_preserves_historical_batch_without_retention_claim(
    project, monkeypatch, shape,
):
    tools, _, packet = prepare(project)
    target = git(project['app'], 'rev-parse', 'HEAD')
    saved = []

    def historical_block(self, run, repository):
        blocked = run.publication_blocked('test_publication_blocked', {})
        historical = replace(blocked, history=tuple(historical_entries(blocked.history, shape)))
        self._save(run.intent.task_id, historical, run.version)
        saved.extend(deepcopy(historical.history))
        return historical

    monkeypatch.setattr(RuntimeResultIntegration, '_publish', historical_block)
    result = tools.invoke(packet)
    assert result['phase'] == 'publication_failed'
    assert result['history'] == saved
    assert 'acceptance_manifests' not in result
    assert git(project['app'], 'rev-parse', 'HEAD') == target


@pytest.mark.parametrize('shape', ['absent_details', 'absent_check_ids'])
def test_missing_batch_identity_requires_fresh_checks_before_publication(
    project, monkeypatch, shape,
):
    tools, _, packet = prepare(project)
    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, '_publish', block_publication)
        first = tools.invoke(packet)
    history = seed_historical_input(project, shape)
    done = tools.invoke(packet)
    assert done['status'] == 'integrated'
    assert len(done['checks']) == 2
    assert done['checks'][0] == first['checks'][0]
    assert done['checks'][1]['id'] != first['checks'][0]['id']
    assert done['checks'][1]['passed'] is True
    assert done['history'][:len(history)] == history
    assert done['target_after'] == git(project['app'], 'rev-parse', 'HEAD')
