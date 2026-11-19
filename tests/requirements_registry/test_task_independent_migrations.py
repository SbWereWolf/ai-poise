"""Application registry operations do not depend on Task admission."""
from pathlib import Path
import sqlite3
import time

import pytest
from yoyo import read_migrations

from requirements_registry.migration_support import Scene, snapshot, sql, create_database
from requirements_registry.task_independent_support import (
    Application, DATA, assert_command_effect, expected_empty, file_state,
    history_backend, history_ids, second_application, start_lock, success, task_state,
    application_observation, assert_application_unchanged, assert_bootstrap_replay,
)


@pytest.fixture
def app(project):
    return Application(project)


def test_project_configuration_and_query_control(app):
    app.assert_taskless()
    assert snapshot(app.database) == expected_empty()
    result = app.query()
    assert result['revision'] == 0
    registry = result['results'][0]['value']
    assert registry['requirements'] == {} and registry['links'] == []
    assert not app.cwd.is_relative_to(app.project['app'])
    app.assert_taskless()


def test_standard_history_fixture_control(app):
    app.assert_taskless()
    app.standard_seed()
    expected = expected_empty()
    expected['requirements'] = [('ONE', 'system', 'current', 'First')]
    assert snapshot(app.database) == expected
    assert history_ids(app.database) == [('0200-one',)]
    backend = history_backend(app.database)
    try:
        migrations = read_migrations(str(app.history))
        backend.rollback_migrations(backend.to_rollback(migrations))
        assert snapshot(app.database) == expected_empty()
        backend.apply_migrations(backend.to_apply(migrations))
        assert snapshot(app.database) == expected
    finally:
        backend.connection.close()
    app.assert_taskless()


def test_command_does_not_require_task(app):
    app.query()
    before = snapshot(app.database)
    app.assert_taskless()
    result = success(app.command())
    assert result['migrations'] == ['0100-taskless']
    assert_command_effect(app, before)
    assert app.query()['revision'] == 1
    app.assert_taskless()


@pytest.mark.parametrize('state', ['newborn', 'readonly', 'foreign'])
def test_unrelated_task_state_does_not_gate_registry_write(project, state):
    if state == 'newborn':
        from tasks.test_newborn_lifecycle import create, tools
        owner = tools(project, 'unrelated-owner')
        assert create(owner, 'UNRELATED')['status'] == 'newborn'
        worktrees = []
    else:
        scene = Scene(project, read_only=state == 'readonly')
        worktrees = [scene.worktree]
        if state == 'foreign':
            _, _, context = scene.foreign_context()
            worktrees.append(Path(context['worktree']))
    app = Application(project, empty=False)
    before = snapshot(app.database)
    tasks = task_state(project)
    files = [file_state(path) for path in worktrees]
    app.query()
    success(app.command())
    assert_command_effect(app, before)
    assert task_state(project) == tasks
    assert [file_state(path) for path in worktrees] == files


def test_selected_application_owns_requirements_history(app, tmp_path, monkeypatch):
    other = second_application(tmp_path, monkeypatch)
    assert app.cfg['project'] != other.cfg['project']
    # The independently registered configuration identities/roots are distinct.
    assert app.config != other.config
    for key in ['requirements_database', 'requirements_lock']:
        assert (app.project['root'] / app.cfg['paths']['state'] / app.cfg['paths'][key]) != (
            other.project['root'] / other.cfg['paths']['state'] / other.cfg['paths'][key])
    assert app.history != other.history and app.bootstrap != other.bootstrap
    other_before = application_observation(other)
    success(app.command())
    assert_command_effect(app, expected_empty())
    assert_application_unchanged(other, other_before)
    first_after = application_observation(app)
    success(other.command('other_command'))
    assert sql(other.database, 'SELECT id FROM requirements ORDER BY id') == [('ONLY-B',)]
    assert sql(other.database, 'SELECT revision FROM requirements_meta') == [(1,)]
    assert (other.history / '0100-other.py').is_file()
    assert_application_unchanged(app, first_after)
    app.assert_taskless()
    other.assert_taskless()


def test_apply_does_not_require_task(app):
    from requirements_registry.migration_support import migration
    step = DATA['steps'][0]
    migration(app.history, step['name'], step['forward'], step['backward'])
    result = success(app.invoke('apply', {'kind': 'data'}))
    assert result['migrations'] == ['0200-one']
    expected = expected_empty()
    expected['requirements'] = [('ONE', 'system', 'current', 'First')]
    assert snapshot(app.database) == expected
    assert history_ids(app.database) == [('0200-one',)]
    app.assert_taskless()


def test_rollback_does_not_require_task(app):
    app.standard_seed()
    before = snapshot(app.database)
    result = success(app.invoke('rollback', {'kind': 'data'}))
    assert result['migrations'] == ['0200-one']
    assert snapshot(app.database) == expected_empty()
    assert history_ids(app.database) == []
    success(app.invoke('apply', {'kind': 'data'}))
    assert snapshot(app.database) == before
    assert sql(app.database, DATA['history_log_query']) == [tuple(r) for r in DATA['roundtrip_log']]
    app.assert_taskless()


def test_squash_does_not_require_task(app):
    app.standard_seed(3)
    before = snapshot(app.database)
    result = success(app.invoke('squash', {
        'name': '0300-squashed', 'migrations': ['0201-two', '0202-three'],
    }))
    assert result['migrations'] == ['0300-squashed']
    assert snapshot(app.database) == before
    assert (app.history / '0200-one.py').is_file()
    assert not (app.history / '0201-two.py').exists()
    assert not (app.history / '0202-three.py').exists()
    assert history_ids(app.database) == [('0200-one',), ('0300-squashed',)]
    success(app.invoke('rollback', {'kind': 'data'}))
    assert snapshot(app.database) == expected_empty()
    success(app.invoke('apply', {'kind': 'data'}))
    assert snapshot(app.database) == before
    app.assert_taskless()


def test_rebase_does_not_require_task(app):
    app.standard_seed(2)
    # Test-owned nonempty ancillary data exercises full snapshot preservation.
    for statement in DATA['ancillary_sql']:
        sql(app.database, statement)
    before = snapshot(app.database)
    result = success(app.invoke('rebase', {'name': '0300-rebased'}))
    assert result['migrations'] == ['0300-rebased']
    assert snapshot(app.database) == before
    assert sorted(p.name for p in app.history.iterdir() if p.suffix == '.py') == ['0300-rebased.py']
    assert history_ids(app.database) == [('0300-rebased',)]
    replay = app.cwd / 'bootstrap-check.sqlite'
    assert_bootstrap_replay(app.bootstrap, app.history, replay, before)
    success(app.invoke('rollback', {'kind': 'data'}))
    assert snapshot(app.database) == expected_empty()
    success(app.invoke('apply', {'kind': 'data'}))
    assert snapshot(app.database) == before
    app.assert_taskless()


@pytest.mark.parametrize('selection', ['all', 'selected'])
def test_export_does_not_require_task(app, selection):
    source = app.cwd / 'export-source.sqlite'
    create_database(source, populated=True)
    original = source.read_bytes()
    before = snapshot(app.database)
    result = success(app.invoke('export', {
        'source': str(source), 'requirements': None if selection == 'all' else ['APP-A'],
        'name': '0300-exported',
    }), 'generated')
    assert result['migration'] == str(app.history / '0300-exported.py')
    assert snapshot(app.database) == before and source.read_bytes() == original
    success(app.invoke('apply', {'kind': 'data'}))
    expected = {name: [tuple(r) for r in rows] for name, rows in DATA['export_expected'][selection].items()}
    assert snapshot(app.database) == expected
    success(app.invoke('apply', {'kind': 'data'}))
    assert snapshot(app.database) == expected and source.read_bytes() == original
    app.assert_taskless()


def test_failed_project_write_keeps_database_atomic(app):
    app.standard_seed()
    before, applied = snapshot(app.database), history_ids(app.database)
    code, result = app.command('failing_command')
    assert code != 0 and result['status'] == 'rejected', result
    assert 'missing_table' in result['reason'], result
    assert snapshot(app.database) == before and history_ids(app.database) == applied
    assert (app.history / '0400-failing.py').is_file()
    app.assert_taskless()


def test_project_write_respects_yoyo_lock(app):
    app.standard_seed()
    app.query()
    before, applied = snapshot(app.database), history_ids(app.database)
    child = start_lock(app)
    try:
        started = time.monotonic()
        code, result = app.invoke('apply', {'kind': 'data'})
        elapsed = time.monotonic() - started
        assert code != 0 and result['status'] == 'rejected', result
        assert 'yoyo' in result['reason'].lower() and 'блокиров' in result['reason'].lower(), result
        assert elapsed < 10
        assert snapshot(app.database) == before and history_ids(app.database) == applied
    finally:
        stdout, stderr = child.communicate('\n', timeout=5)
        assert child.returncode == 0 and stderr == '' and stdout == ''
    app.assert_taskless()
