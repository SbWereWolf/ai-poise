"""Public registration contracts, real Requirements producer and SQLite rollback."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from apps.sequencing.tests import test_store_migration as historical

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/sequencing/fixtures'
DATA = json.loads((FIXTURES / 'registration-contract.json').read_text())
CASES = DATA['cases']


def current_state(path):
    """Capture actual schema and all rows for equality, never a behavior oracle."""
    with closing(sqlite3.connect(path)) as db:
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        rows = {}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            fields = [row[1] for row in db.execute('PRAGMA table_info(' + historical.quoted(table) + ')')]
            selected = ','.join(map(historical.quoted, fields))
            rows[table] = sorted(db.execute('SELECT ' + selected + ' FROM ' + historical.quoted(table)).fetchall(),
                                 key=historical.canonical)
        return {'schema': schema, 'rows': rows, 'version': db.execute('PRAGMA user_version').fetchone()[0]}


def assert_legacy_rows(path, expected):
    """All old data remains; provenance is checked through complete public views."""
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        for table, columns in historical.DATA['columns'].items():
            if table == 'tasks':
                columns = ['task_id', 'state', 'terminal_json']
                positions = [historical.DATA['columns'][table].index(field) for field in columns]
                wanted = [[row[index] for index in positions] for row in expected[table]]
            else:
                wanted = expected[table]
            selected = ','.join(map(historical.quoted, columns))
            actual = [list(row) for row in db.execute('SELECT ' + selected + ' FROM ' + historical.quoted(table))]
            assert sorted(actual, key=historical.canonical) == sorted(wanted, key=historical.canonical), table


class Client:
    def __init__(self, root, application='sequencing'):
        self.root = root
        self.application = application
        (root / 'config').mkdir(parents=True)
        (root / 'state').mkdir()
        self.config = root / 'config' / (application + '.json')
        self.config.write_text(json.dumps(DATA['configs'][application], ensure_ascii=False))
        self.database = root / 'state' / (application + '.sqlite')

    def invoke(self, packet, exit_code=0):
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'apps' / self.application / 'bin' / self.application),
            '--config', str(self.config)], input=json.dumps(packet, ensure_ascii=False) + '\n', cwd=self.root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), text=True, capture_output=True, timeout=30)
        assert process.returncode == exit_code, (process.stdout, process.stderr)
        assert process.stderr == ''
        return json.loads(process.stdout)

    def check(self, scenario, exit_code=0):
        actual = self.invoke(scenario['packet'], exit_code)
        assert actual == scenario['expected']
        return actual

    def seed(self, name):
        seed = DATA['seeds'][name]
        with closing(historical.connect(self.database)) as db:
            db.executescript((FIXTURES / 'format1-source.sql').read_text())
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', 0))
            db.executemany('INSERT INTO tasks(task_id,requirements_snapshot_revision,state,terminal_json) '
                           'VALUES(?,?,?,?)', [(identifier, 2, 'available', None) for identifier in seed['ids']])
            db.commit()
        self.check(seed['warmup'])
        return self

    def reject_unchanged(self, scenario):
        before = current_state(self.database)
        self.check(scenario, 2)
        assert current_state(self.database) == before


def test_goal_only_real_producer_registers_independent_and_sprint(tmp_path):
    req = Client(tmp_path / 'requirements', 'requirements')
    seq = Client(tmp_path / 'sequencing')
    case = CASES['goal_only']
    issued = [req.check(step)['task_id'] for step in case['req_create']]
    view = req.check(case['req_view'])
    before = current_state(req.database)
    packet = deepcopy(case['register'])
    statements = {item['value']['task_id']: item['value'] for item in view['results']
                  if 'snapshot_revision' in item['value']}
    for operation, identifier in zip(packet['packet']['input']['operations'][:3], issued):
        operation['task_id'] = identifier
        operation['requirements_statement_revision'] = statements[identifier]['statement_revision']
        assert statements[identifier]['snapshot_revision'] is None
    packet['packet']['input']['operations'][3]['members'] = [issued[2], issued[1]]
    seq.check(packet)
    seq.check(case['read'])
    assert req.check(case['req_view']) == view
    assert current_state(req.database) == before
    with closing(sqlite3.connect(seq.database)) as db:
        assert db.execute('SELECT task_id,count(*) FROM tasks GROUP BY task_id ORDER BY task_id').fetchall() == [
            (identifier, 1) for identifier in issued]
    assert sorted(path.name for path in tmp_path.iterdir()) == ['requirements', 'sequencing']


def test_published_snapshot_and_unpublished_statement_keep_distinct_counters(tmp_path):
    req = Client(tmp_path / 'requirements', 'requirements')
    seq = Client(tmp_path / 'sequencing')
    case = CASES['published']
    for step in case['req_steps']:
        req.check(step)
    view = req.invoke(case['req_view'])
    published, snapshot, draft = [item['value'] for item in view['results']]
    assert published['statement_revision'] == 2
    assert published['snapshot_revision'] == 1
    assert draft['statement_revision'] == 1 and draft['snapshot_revision'] is None
    before = current_state(req.database)
    packet = deepcopy(case['register'])
    packet['packet']['input']['operations'][0]['task_id'] = published['task_id']
    packet['packet']['input']['operations'][0]['requirements_snapshot_revision'] = published['snapshot_revision']
    packet['packet']['input']['operations'][1]['task_id'] = draft['task_id']
    packet['packet']['input']['operations'][1]['requirements_statement_revision'] = draft['statement_revision']
    assert snapshot is not None
    seq.check(packet)
    seq.check(case['read'])
    assert req.invoke(case['req_view']) == view
    assert current_state(req.database) == before


def test_statement_registration_noop_and_original_receipt_replay(tmp_path):
    case = CASES['statement_replay']
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['first'])
    client.check(case['duplicate'])
    before = current_state(client.database)
    client.check(case['replay'])
    assert current_state(client.database) == before
    client.check(case['read'])


@pytest.mark.parametrize('case', CASES['reference_conflicts'], ids=lambda case: case['name'])
def test_reference_origin_is_immutable(tmp_path, case):
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['first'])
    client.reject_unchanged(case['failure'])


@pytest.mark.parametrize('case', CASES['invalid_references'], ids=lambda case: case['name'])
def test_reference_requires_one_positive_integer_origin(tmp_path, case):
    client = Client(tmp_path).seed('anchor')
    before = current_state(client.database)
    response = client.invoke(case['packet'], 2)
    assert set(response) == {'schema', 'status', 'reason'}
    assert response['schema'] == 'stride-sequencing/v1' and response['status'] == 'rejected'
    assert isinstance(response['reason'], str) and response['reason'].strip()
    assert current_state(client.database) == before
    client.check(CASES['statement_replay']['first'])


def test_parallel_roots_are_registered_and_sorted_lexically(tmp_path):
    case = CASES['parallel']
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['register'])
    client.check(case['read'])
    with closing(sqlite3.connect(client.database)) as db:
        assert db.execute('SELECT sprint_id,revision FROM sprint_revisions').fetchall() == [('SPR-TEST', 1)]


def test_sprint_definition_is_a_set_and_original_replay_is_read_only(tmp_path):
    case = CASES['sprint_replay']
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['first'])
    client.check(case['duplicate'])
    before = current_state(client.database)
    client.check(case['replay'])
    assert current_state(client.database) == before
    client.check(case['read'])
    with closing(sqlite3.connect(client.database)) as db:
        assert db.execute('SELECT sprint_id,revision FROM sprint_revisions').fetchall() == [('SPR-TEST', 1)]


@pytest.mark.parametrize('case', CASES['sprint_conflicts'], ids=lambda case: case['name'])
def test_sprint_identity_and_single_membership(tmp_path, case):
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['first'])
    client.reject_unchanged(case['failure'])


@pytest.mark.parametrize('case', CASES['invalid_graphs'], ids=lambda case: case['name'])
def test_invalid_graph_rolls_back_registration_prefix_and_receipt(tmp_path, case):
    client = Client(tmp_path).seed(case['seed'])
    client.reject_unchanged(case['failure'])


def populate_literal_format1(client):
    with closing(historical.connect(client.database)) as db:
        db.executescript((FIXTURES / 'format1-source.sql').read_text())
        historical.insert_rows(db, historical.rendered(historical.DATA['target_rows'], client.root),
                               historical.DATA['columns'])
        db.commit()


def test_populated_format1_preserves_public_views_receipts_and_real_wip(tmp_path):
    client = Client(tmp_path)
    repository, worktree = historical.physical_wip(tmp_path)
    populate_literal_format1(client)
    before_wip = historical.wip_state(tmp_path, repository, worktree)
    packet = historical.DATA['packets']['populated_query']
    expected = historical.rendered(historical.DATA['responses']['populated_query'], tmp_path)
    assert client.invoke(packet) == expected
    assert_legacy_rows(client.database, historical.rendered(historical.DATA['target_rows'], tmp_path))
    before = current_state(client.database)
    assert client.invoke(packet) == expected
    assert current_state(client.database) == before
    assert client.invoke(historical.DATA['packets']['source_replay']) == historical.DATA['responses']['source_replay']
    assert current_state(client.database) == before
    assert historical.wip_state(tmp_path, repository, worktree) == before_wip


def test_late_format_marker_failure_restores_source_and_closes_connection(tmp_path, monkeypatch):
    from apps.sequencing import shell
    client = Client(tmp_path)
    repository, worktree = historical.physical_wip(tmp_path)
    populate_literal_format1(client)
    before = current_state(client.database)
    before_wip = historical.wip_state(tmp_path, repository, worktree)
    original_connect = sqlite3.connect
    observed = {}
    connections = []

    def factory(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        connections.append(db)
        def authorizer(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_PRAGMA and first == 'user_version' and second == '2':
                observed['schema'] = db.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
                observed['rows'] = [tuple(row) for row in db.execute(
                    'SELECT task_id,state,terminal_json FROM tasks ORDER BY task_id')]
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorizer)
        return db

    config = shell.load_config(client.config)
    store = shell.Store(config)
    with monkeypatch.context() as patch:
        patch.setattr(shell.sqlite3, 'connect', factory)
        try:
            with store.locked():
                with pytest.raises(sqlite3.Error):
                    store.connect()
            assert observed['schema'] != before['schema']
            positions = [historical.DATA['columns']['tasks'].index(field)
                         for field in ['task_id', 'state', 'terminal_json']]
            assert observed['rows'] == sorted(tuple(row[index] for index in positions)
                for row in historical.rendered(historical.DATA['target_rows']['tasks'], tmp_path))
            for db in connections:
                with pytest.raises(sqlite3.ProgrammingError):
                    db.execute('SELECT 1')
        finally:
            for db in connections:
                db.close()
    assert current_state(client.database) == before
    assert historical.wip_state(tmp_path, repository, worktree) == before_wip
    expected = historical.rendered(historical.DATA['responses']['populated_query'], tmp_path)
    assert client.invoke(historical.DATA['packets']['populated_query']) == expected
    assert_legacy_rows(client.database, historical.rendered(historical.DATA['target_rows'], tmp_path))


@pytest.mark.parametrize('case', CASES['request_failures'], ids=lambda case: case['name'])
def test_request_reuse_and_stale_revision_preserve_all_state(tmp_path, case):
    client = Client(tmp_path).seed(case['seed'])
    client.check(case['first'])
    client.check(case['advance'])
    client.reject_unchanged(case['failure'])
