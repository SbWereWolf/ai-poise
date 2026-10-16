"""Integration boundary: app factory + public Commands; SQL owns setup/oracles."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from apps.requirements.storage import RequirementsStore
from poise.application.requirements_registry import RequirementsCommands
from poise.modules.foundation.errors import PoiseError

FIXTURES = ROOT / 'apps/requirements/fixtures'
STATE = json.loads((FIXTURES / 'graph-state.json').read_text())
QUERIES = json.loads((FIXTURES / 'graph-queries.json').read_text())
MUTATIONS = json.loads((FIXTURES / 'graph-mutations.json').read_text())
REJECTIONS = json.loads((FIXTURES / 'graph-rejections.json').read_text())
FRESH = json.loads((FIXTURES / 'graph-fresh.json').read_text())


def quoted(value):
    # Table/column names come from the independent test-owned fixture only.
    return '"' + value.replace('"', '""') + '"'


class Environment:
    def __init__(self, root, seed=True):
        self.root = root
        self.database = root / 'state/requirements.sqlite'
        self.lock = root / 'state/requirements.lock'
        self.database.parent.mkdir()
        if seed:
            with sqlite3.connect(self.database) as db:
                db.executescript((FIXTURES / 'storage-schema.sql').read_text())
                for table, data in STATE['tables'].items():
                    columns = ','.join(quoted(v) for v in data['columns'])
                    parameters = ','.join('?' for _ in data['columns'])
                    db.executemany(
                        f'INSERT INTO {quoted(table)}({columns}) VALUES({parameters})',
                        data['rows'],
                    )
                db.commit()
                # Prove the literal arrangement is supported before invoking behavior.
                assert db.execute('PRAGMA foreign_key_check').fetchall() == []
                assert db.execute(
                    'SELECT current_statement_revision FROM task_identities WHERE task_id=?',
                    ('000007',),
                ).fetchone() == (2,)
        self.reopen()

    def reopen(self):
        self.commands = RequirementsCommands(
            RequirementsStore(self.database, self.lock, 0.05, 0.005), 64,
        )

    def state(self):
        with sqlite3.connect(self.database) as db:
            result = {}
            for table, data in STATE['tables'].items():
                columns = ','.join(quoted(v) for v in data['columns'])
                result[table] = db.execute(
                    f'SELECT {columns} FROM {quoted(table)} ORDER BY {columns}',
                ).fetchall()
            return result

    def check_sql(self, spec):
        with sqlite3.connect(self.database) as db:
            rows = db.execute(spec['sql'], spec['parameters']).fetchall()
        assert [list(row) for row in rows] == spec['rows']

    def snapshot(self):
        response = self.commands.query([MUTATIONS['snapshot_query']])
        assert response['results'][0]['value'] == MUTATIONS['snapshot_value']

    def shell(self, queries):
        config_dir = self.root / 'config'
        config_dir.mkdir(exist_ok=True)
        config = config_dir / 'requirements.json'
        settings = json.loads((FIXTURES / 'requirements-config.json').read_text())
        settings['lock'] = {'seconds': 0.05, 'poll_seconds': 0.005}
        config.write_text(json.dumps(settings, ensure_ascii=False) + '\n')
        env = os.environ.copy()
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.run(
            [str(ROOT / 'apps/requirements/bin/requirements'), '--config', str(config)],
            input=json.dumps({'operation': 'query', 'input': {'queries': queries}}, ensure_ascii=False),
            text=True, capture_output=True, cwd=self.root, env=env, check=False,
        )


@pytest.fixture
def environment(tmp_path):
    return Environment(tmp_path)


def test_existing_two_level_control(environment):
    """An independent proper fixture and unchanged first-edge consumer work now."""
    before = environment.state()
    assert environment.commands.query(QUERIES['control']['queries']) == QUERIES['control']['response']
    assert environment.state() == before


def test_fresh_empty_store_control(tmp_path):
    fresh = Environment(tmp_path, seed=False)
    assert fresh.commands.query(QUERIES['empty_registry_queries']) == QUERIES['empty_registry']
    with sqlite3.connect(fresh.database) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (1,)
        assert db.execute(
            "SELECT count(name) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        ).fetchone() == (20,)


def test_existing_second_system_parent_control(environment):
    case = MUTATIONS['add_system_control']
    assert environment.commands.apply(case['request']) == {'revision': 8, 'replayed': False}
    assert environment.commands.query(case['queries']) == case['response']
    environment.check_sql(case['sql'])


@pytest.mark.parametrize('case', QUERIES['cases'], ids=lambda case: case['id'])
def test_graph_query(environment, case):
    before = environment.state()
    assert environment.commands.query(case['queries']) == case['response']
    assert environment.state() == before
    environment.reopen()
    assert environment.commands.query(case['queries']) == case['response']
    assert environment.state() == before


@pytest.mark.parametrize('case', MUTATIONS['cases'], ids=lambda case: case['id'])
def test_graph_mutation(environment, case):
    assert environment.commands.apply(case['request']) == case['response']
    assert environment.commands.query(case['queries']) == case['query_response']
    for observation in case['sql']:
        environment.check_sql(observation)
    environment.snapshot()
    after = environment.state()
    environment.reopen()
    assert environment.commands.query(case['queries']) == case['query_response']
    environment.snapshot()
    assert environment.state() == after


def test_graph_unlink_relink_preserves_closed_period(environment):
    case = MUTATIONS['relink']
    assert environment.commands.apply(case['first']) == {'revision': 8, 'replayed': False}
    assert environment.commands.apply(case['second']) == {'revision': 9, 'replayed': False}
    environment.check_sql(case['sql'])
    environment.snapshot()
    before = environment.state()
    environment.reopen()
    environment.check_sql(case['sql'])
    assert environment.state() == before


@pytest.mark.parametrize('case', REJECTIONS['cases'], ids=lambda case: case['id'])
def test_graph_rejection(environment, case):
    before = environment.state()
    with pytest.raises(PoiseError) as failure:
        getattr(environment.commands, case['method'])(deepcopy(case['input']))
    message = str(failure.value)
    for token in case['message_tokens']:
        assert token.lower() in message.lower(), message
    assert environment.state() == before
    environment.reopen()
    assert environment.state() == before


def test_graph_exact_replay_after_later_change(environment):
    case = MUTATIONS['replay']
    assert environment.commands.apply(case['first']) == {'revision': 8, 'replayed': False}
    assert environment.commands.apply(case['later']) == {'revision': 9, 'replayed': False}
    before = environment.state()
    assert environment.commands.apply(case['first']) == case['replayed_response']
    assert environment.state() == before
    environment.reopen()
    assert environment.commands.apply(case['first']) == case['replayed_response']
    assert environment.state() == before


@pytest.mark.parametrize('field', ['conflict', 'type_conflict'])
def test_graph_conflicting_replay_preserves_state(environment, field):
    case = MUTATIONS['replay']
    assert environment.commands.apply(case['first']) == {'revision': 8, 'replayed': False}
    before = environment.state()
    with pytest.raises(PoiseError) as failure:
        environment.commands.apply(case[field])
    assert 'request' in str(failure.value).lower() or 'integer' in str(failure.value).lower()
    assert environment.state() == before


def test_graph_public_shell_returns_real_task_parents(environment):
    case = QUERIES['cases'][0]
    process = environment.shell(case['queries'])
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert process.stderr == ''
    body = json.loads(process.stdout)
    assert body.pop('status') == 'read_only'
    assert body == case['response']


def test_fresh_graph_queries(tmp_path):
    """Ordinary initialized empty store supports all new graph read owners."""
    fresh = Environment(tmp_path, seed=False)
    assert fresh.commands.query(FRESH['queries']) == FRESH['response']
    fresh.reopen()
    assert fresh.commands.query(FRESH['queries']) == FRESH['response']
