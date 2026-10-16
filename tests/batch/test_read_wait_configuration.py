"""Explicit installation policy for physical read admission."""
import json
from pathlib import Path
import pytest
from poise.common import PoiseError, load_config
from batch.test_public_read_transactions import SOURCE, state_paths


def test_read_wait_is_required_in_common_project_config(project):
    config = json.loads(project['config_path'].read_text())
    config['limits'].pop('sqlite_read_seconds', None)
    project['config_path'].write_text(json.dumps(config))
    with pytest.raises(PoiseError, match='sqlite_read_seconds'):
        load_config(project['config_path'])


@pytest.mark.parametrize('value', [0, -1, False, '1', float('inf')])
def test_read_wait_rejects_nonpositive_nonfinite_or_non_numeric_values(
        project, value):
    config = json.loads(project['config_path'].read_text())
    config['limits']['sqlite_read_seconds'] = value
    project['config_path'].write_text(json.dumps(config))
    with pytest.raises(PoiseError, match='sqlite_read_seconds'):
        load_config(project['config_path'])


def test_read_wait_is_independent_from_writer_wait(project):
    config = json.loads(project['config_path'].read_text())
    config['limits']['sqlite_read_seconds'] = 0.25
    config['limits']['lock_seconds'] = 0.01
    project['config_path'].write_text(json.dumps(config))
    _, loaded, _ = load_config(project['config_path'])
    assert loaded['limits']['sqlite_read_seconds'] == 0.25
    assert loaded['limits']['lock_seconds'] == 0.01


@pytest.mark.parametrize('name', ['project.example.json',
                                  'project-templates/linux-reference.json',
                                  'project-templates/wsl-poise.json'])
def test_shipped_project_starters_declare_read_wait(name):
    starter = json.loads(
        (SOURCE.parent / 'config' / name).read_text())
    config = starter if name == 'project.example.json' else starter[
        'config']
    assert config['limits']['sqlite_read_seconds'] > 0


@pytest.mark.parametrize('kind', ['task', 'requirements', 'hook'])
@pytest.mark.parametrize('wait, succeeds',
                         [(0.02, False), (0.25, True)])
def test_configured_read_budget_controls_actual_sqlite_busy_admission(
        project, tmp_path, monkeypatch, kind, wait, succeeds):
    import sqlite3
    from batch.test_public_read_transactions import environment, \
        invoke, query_state, state_paths
    from poise.infrastructure.clock import SystemClock
    from poise.infrastructure.session_establishment import \
        establish_direct_poise
    from poise.infrastructure.sqlite import transaction

    assert invoke(project,
                  'public-read-bootstrap.json').returncode == 0
    hook = None
    if kind == 'hook':
        from batch.test_public_read_transactions import prepare_hook
        hook, binding = prepare_hook(project, tmp_path)
    config = json.loads(project['config_path'].read_text())
    config['limits']['sqlite_read_seconds'] = wait
    config['limits']['lock_seconds'] = 0.01
    project['config_path'].write_text(json.dumps(config))
    task_database = state_paths(project)[0]
    before = query_state(task_database)
    database = task_database if kind == 'task' else project['root'] / \
                                                    config['paths'][
                                                        'state'] / \
                                                    config['paths'][
                                                        'requirements_database']
    if hook is not None:
        database = hook.settings.database
    writer = sqlite3.connect(database, timeout=0,
                             isolation_level=None)
    writer.execute('BEGIN EXCLUSIVE')
    elapsed = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
        if elapsed[0] >= 0.04:
            writer.execute('ROLLBACK')

    monkeypatch.setattr(transaction, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(transaction, 'sleep', sleep)

    def read():
        if hook is None:
            return establish_direct_poise(project['config_path'],
                                          environment(project),
                                          SystemClock(),
                                          read_only=True)
        record, latest = hook.registry.get(binding['session_id'])
        assert record['session_id'] == binding['session_id']
        assert latest is not None

    try:
        if succeeds:
            read()
            assert elapsed[0] >= 0.04
        else:
            with pytest.raises(PoiseError) as rejected:
                read()
            assert 'sqlite_read_seconds=0.02' in str(rejected.value)
            assert elapsed[0] == pytest.approx(0.02)
    finally:
        if writer.in_transaction:
            writer.execute('ROLLBACK')
        writer.close()
    assert sleeps
    assert query_state(task_database) == before


@pytest.mark.parametrize('value',
                         [None, [], {}, float('nan'), float('-inf'),
                          10 ** 1000],
                         ids=['null', 'list', 'object', 'nan',
                              'negative-infinity',
                              'unrepresentable-integer'])
def test_read_wait_rejects_malformed_or_unrepresentable_values_before_effects(
        project, value):
    config = json.loads(project['config_path'].read_text())
    config['limits']['sqlite_read_seconds'] = value
    project['config_path'].write_text(json.dumps(config))
    database, lock = state_paths(project)
    assert not database.exists()
    assert not lock.exists()
    with pytest.raises(PoiseError):
        load_config(project['config_path'])
    assert not database.exists()
    assert not lock.exists()


def test_read_wait_accepts_explicit_positive_integer(project):
    config = json.loads(project['config_path'].read_text())
    config['limits']['sqlite_read_seconds'] = 3
    project['config_path'].write_text(json.dumps(config))
    _, loaded, _ = load_config(project['config_path'])
    assert loaded['limits']['sqlite_read_seconds'] == 3
    assert type(loaded['limits']['sqlite_read_seconds']) is int
