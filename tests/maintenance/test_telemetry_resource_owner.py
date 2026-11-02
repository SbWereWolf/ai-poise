"""The resource CLI recognizes its canonical owner's supported stores."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from poise.common import configured_root
from poise.infrastructure.telemetry import TelemetryDatabase


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def selected(project):
    state = Path(configured_root(project['config_path'].parent,
                                 project['cfg']['paths']['state']))
    path = state / project['cfg']['accounting']['storage']['database']
    path.parent.mkdir(parents=True, exist_ok=True)
    return project, state, path


def action(selected, operation):
    project, state, _ = selected
    reply = subprocess.run([
        sys.executable, '-B', '-m', 'poise_environment.repairs.telemetry_database',
        '--config', str(project['config_path']), '--state-root', str(state),
        '--requirement-id', 'poise-telemetry-database', '--action', operation,
        '--schema', 'poise/maintenance-action/v2', '--recommendations',
        str(ROOT / 'config/maintenance/recommendations.example.json'),
    ], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
        capture_output=True)
    return reply, json.loads(reply.stdout)


@pytest.mark.parametrize('format_name', ['current', 'legacy'])
def test_supported_existing_store_is_observed_without_changes(selected, format_name):
    _, _, path = selected
    with TelemetryDatabase(path, path.with_suffix('.lock'), 2.0, 0.01).transaction() as db:
        db.execute("INSERT INTO accounting_usage VALUES(?,?,?,?,?,?,?,?)",
                   ('literal-event', None, 'fixture', 'fixture', 'sample', 1,
                    '2000-01-01T00:00:00Z', '{"count":7}'))
    if format_name == 'legacy':
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY); '
                             'CREATE TABLE task_workflows(id TEXT PRIMARY KEY); '
                             'CREATE TABLE sessions(id TEXT PRIMARY KEY); '
                             'PRAGMA application_id=0; PRAGMA user_version=13;')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for operation in ('check', 'install'):
        reply, value = action(selected, operation)
        assert reply.returncode == 0, (reply.stdout, reply.stderr)
        assert value.get('state', value.get('outcome')) == (
            'satisfied' if operation == 'check' else 'completed'), value
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT id,data FROM accounting_usage').fetchall() == [
            ('literal-event', '{"count":7}')]


def test_absent_store_check_and_creation_remain_owned(selected):
    _, _, path = selected
    reply, value = action(selected, 'check')
    assert reply.returncode == 0 and value['state'] == 'missing'
    assert not path.exists()
    reply, value = action(selected, 'install')
    assert reply.returncode == 0 and value['outcome'] == 'completed'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA application_id').fetchone() == (1346456881,)
        assert db.execute('PRAGMA user_version').fetchone() == (1,)
        assert db.execute('SELECT id FROM accounting_usage').fetchall() == []
        assert db.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchall() == []


@pytest.mark.parametrize('kind', ['unsupported', 'corrupt'])
def test_unsupported_existing_store_is_refused_and_preserved(selected, kind):
    _, _, path = selected
    if kind == 'unsupported':
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE alien(id); PRAGMA user_version=99;')
    else:
        path.write_bytes(b'not a sqlite database\n')
    before = path.read_bytes()
    for operation in ('check', 'install'):
        reply, value = action(selected, operation)
        assert reply.returncode == (0 if operation == 'check' else 2)
        assert value.get('state', value.get('outcome')) == 'unavailable'
        assert path.read_bytes() == before
