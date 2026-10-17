"""Public standalone Task creation; independent JSON and direct SQL oracles."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/requirements/fixtures'
DATA = json.loads((FIXTURES / 'task-creation.json').read_text(encoding='utf-8'))


class Scenario:
    def __init__(self, root):
        self.root = root
        config_dir = root / 'config'
        config_dir.mkdir()
        self.config = config_dir / 'requirements.json'
        self.database = root / 'state/requirements.sqlite'
        settings = json.loads((FIXTURES / 'requirements-config.json').read_text())
        settings['task_ids'] = deepcopy(DATA['policy'])
        self.config.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        assert self.cli('query', DATA['initial_query']) == DATA['initial_response']

    def start(self, operation, body):
        process = subprocess.Popen(
            [sys.executable, '-B', str(ROOT / 'apps/requirements/bin/requirements'),
             '--config', str(self.config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=self.root,
        )
        process.stdin.write(json.dumps({'operation': operation, 'input': body}, ensure_ascii=False))
        process.stdin.close()
        process.stdin = None
        return process

    @staticmethod
    def finish(process, exit_code=0):
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == exit_code, stdout + stderr
        assert stderr == ''
        return json.loads(stdout)

    def cli(self, operation, body, exit_code=0):
        return self.finish(self.start(operation, body), exit_code)

    def apply(self, request_id, revision, operations):
        body = {'request_id': request_id, 'expected_revision': revision, 'operations': operations}
        assert self.cli('apply', body) == {
            'status': 'applied', 'revision': revision + 1, 'replayed': False,
        }
        return body

    def seed_foundation(self):
        self.apply('foundation', 0, deepcopy(DATA['foundation']))

    def create(self, body, expected):
        result = self.cli('create_task', body)
        assert result == expected
        return result['task_id']

    def query(self, kind, task_id, revision, value):
        assert self.cli('query', {'queries': [
            {'id': 'read', 'kind': kind, 'task_id': task_id},
        ]}) == {'status': 'read_only', 'revision': revision,
               'results': [{'id': 'read', 'value': value}]}

    def revise(self, task_id, statement, revision, statement_revision):
        self.apply('edit-' + str(revision), revision, [{
            'kind': 'revise_task', 'task_id': task_id,
            'expected_statement_revision': statement_revision, 'statement': statement,
        }])

    def sql(self, query, parameters=()):
        with sqlite3.connect(self.database) as db:
            return db.execute(query, parameters).fetchall()

    def creation(self, task_id, body, receipt, revision):
        self.query('task_creation', task_id, revision, {
            'task_id': task_id, 'request_id': body['request_id'],
            'intent': body, 'response': receipt,
        })
        rows = self.sql(
            'SELECT creation_request_id,intent,task_id,response '
            'FROM task_creation_requests ORDER BY creation_request_id',
        )
        row = next(row for row in rows if row[0] == body['request_id'])
        assert row[0] == body['request_id']
        assert json.loads(row[1]) == body
        assert row[2] == task_id
        assert json.loads(row[3]) == receipt

    def state(self):
        # SQL dumps preserve all schema/data without production serializers or hashes.
        with sqlite3.connect(self.database) as db:
            return tuple(db.iterdump())

    def reject_unchanged(self, body, reason_words):
        before = self.state()
        response = self.cli('create_task', body, 2)
        assert set(response) == {'status', 'reason'}
        assert response['status'] == 'rejected'
        for word in reason_words:
            assert word in response['reason'].lower()
        assert self.state() == before
        assert self.cli('query', DATA['initial_query'])['status'] == 'read_only'


def test_goal_only_draft_is_readable_with_issued_identity(tmp_path):
    env = Scenario(tmp_path)
    body = deepcopy(DATA['goal_only'])
    task_id = env.create(body, DATA['created_010_1'])
    env.query('get_task', task_id, 1, DATA['empty_task'])
    env.query('task_basis', task_id, 1, DATA['basis_empty'])
    env.creation(task_id, body, DATA['created_010_1'], 1)
    assert env.sql('SELECT task_id,current_statement_revision FROM task_identities') == [('010', 1)]
    rows = env.sql('SELECT task_id,statement_revision,goal,definition_of_done FROM task_statements')
    assert rows == [('010', 1, 'Подготовить отчёт.', '[]')]
    assert env.sql('SELECT task_id,snapshot_revision FROM task_snapshots') == []


def test_linked_task_edit_preserves_identity_goal_and_history(tmp_path):
    env = Scenario(tmp_path)
    env.seed_foundation()
    body = deepcopy(DATA['linked'])
    task_id = env.create(body, DATA['created_010_2'])
    env.query('get_task', task_id, 2, DATA['linked_task'])
    env.query('task_basis', task_id, 2, DATA['basis_linked'])
    env.revise(task_id, deepcopy(DATA['statement_edited']), 2, 1)
    env.query('get_task', task_id, 3, DATA['edited_task'])
    basis = deepcopy(DATA['basis_linked'])
    basis['statement_revision'] = 2
    env.query('task_basis', task_id, 3, basis)
    assert env.sql('SELECT task_id,statement_revision,goal FROM task_statements '
                   'ORDER BY statement_revision') == [
        ('010', 1, 'Подготовить отчёт.'), ('010', 2, 'Подготовить отчёт.'),
    ]
    assert env.sql('SELECT task_id,statement_revision,key,application_id '
                   'FROM application_task_links ORDER BY statement_revision') == [
        ('010', 1, 'report', 'APP-REPORT'), ('010', 2, 'report', 'APP-REPORT'),
    ]
    env.creation(task_id, body, DATA['created_010_2'], 3)


def test_unlinked_draft_becomes_linked_by_explicit_statement_edit(tmp_path):
    env = Scenario(tmp_path)
    env.seed_foundation()
    task_id = env.create(deepcopy(DATA['unlinked']), DATA['created_010_2'])
    env.query('task_basis', task_id, 2, DATA['basis_unlinked'])
    assert env.sql('SELECT application_id FROM application_task_links') == []
    env.revise(task_id, deepcopy(DATA['statement_linked']), 2, 1)
    basis = deepcopy(DATA['basis_linked'])
    basis['statement_revision'] = 2
    env.query('task_basis', task_id, 3, basis)
    env.query('get_task', task_id, 3, {**DATA['linked_task'], 'statement_revision': 2})


def test_configured_ids_collision_and_exhaustion_preserve_existing_tasks(tmp_path):
    env = Scenario(tmp_path)
    first = env.create(deepcopy(DATA['explicit']), DATA['created_010_1'])
    env.reject_unchanged({**DATA['explicit'], 'request_id': 'collision'}, ['exists'])
    second = env.create(deepcopy(DATA['next']), DATA['created_012_2'])
    env.reject_unchanged({'request_id': 'exhausted', 'goal': 'Третий результат.'}, ['exhaust'])
    env.query('get_task', first, 2, DATA['empty_task'])
    env.query('get_task', second, 2, DATA['next_task'])
    assert env.sql('SELECT task_id FROM task_identities ORDER BY task_id') == [('010',), ('012',)]
    assert env.sql('SELECT creation_request_id FROM task_creation_requests '
                   'ORDER BY creation_request_id') == [('explicit',), ('next',)]


def test_creation_replay_retains_original_receipt_after_edit(tmp_path):
    env = Scenario(tmp_path)
    env.seed_foundation()
    body = deepcopy(DATA['linked'])
    task_id = env.create(body, DATA['created_010_2'])
    env.revise(task_id, deepcopy(DATA['statement_edited']), 2, 1)
    before = env.state()
    assert env.cli('create_task', body) == {**DATA['created_010_2'], 'replayed': True}
    assert env.state() == before
    env.creation(task_id, body, DATA['created_010_2'], 3)
    env.reject_unchanged({**body, 'goal': 'Другая цель.'}, ['request'])
    env.query('get_task', task_id, 3, DATA['edited_task'])
    # A fresh request still receives the next configured ID, despite replay/conflict.
    assert env.cli('create_task', DATA['next']) == {**DATA['created_012_2'], 'revision': 4}


@pytest.mark.parametrize('choice', DATA['work_choices'], ids=['standalone', 'sequence'])
def test_explicit_work_choice_roundtrips_without_peer_store_writes(tmp_path, choice):
    peers = [tmp_path / 'sequencing.sqlite', tmp_path / 'execution.sqlite']
    for peer in peers:
        with sqlite3.connect(peer) as db:
            db.execute('CREATE TABLE peer_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    def read_peers():
        snapshots = []
        for peer in peers:
            with sqlite3.connect(peer) as db:
                snapshots.append((
                    db.execute('SELECT name,sql FROM sqlite_master WHERE type=? '
                               'ORDER BY name', ('table',)).fetchall(),
                    db.execute('SELECT id,value FROM peer_state ORDER BY id').fetchall(),
                ))
        return snapshots
    before = read_peers()
    env = Scenario(tmp_path)
    body = {**DATA['goal_only'], 'work_choice': deepcopy(choice)}
    task_id = env.create(body, DATA['created_010_1'])
    env.creation(task_id, body, DATA['created_010_1'], 1)
    env.query('get_task', task_id, 1, DATA['empty_task'])
    assert read_peers() == before
    assert env.sql('SELECT task_id FROM task_identities') == [('010',)]


def test_two_creator_processes_allocate_without_revision_preread(tmp_path):
    env = Scenario(tmp_path)
    bodies = [deepcopy(DATA['goal_only']), deepcopy(DATA['next'])]
    processes = [env.start('create_task', body) for body in bodies]
    # Finish both processes even if the first assertion fails.
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        results.append((process.returncode, stdout, stderr))
    receipts = []
    for code, stdout, stderr in results:
        assert code == 0, stdout + stderr
        assert stderr == ''
        receipts.append(json.loads(stdout))
    assert sorted(row['task_id'] for row in receipts) == ['010', '012']
    assert sorted(row['revision'] for row in receipts) == [1, 2]
    for body, receipt in zip(bodies, receipts):
        assert receipt == {**DATA['created_010_1'], 'task_id': receipt['task_id'],
                           'revision': receipt['revision']}
        expected = {**DATA['empty_task'], 'task_id': receipt['task_id'], 'goal': body['goal']}
        env.query('get_task', receipt['task_id'], 2, expected)
        env.creation(receipt['task_id'], body, receipt, 2)


def test_empty_goal_rejects_without_partial_effects(tmp_path):
    env = Scenario(tmp_path)
    env.reject_unchanged({'request_id': 'empty-goal', 'goal': ''}, ['goal'])
    assert env.sql('SELECT task_id FROM task_identities') == []
    assert env.sql('SELECT revision FROM requirements_meta') == [(0,)]


def test_mixed_basis_requires_all_active_entries_and_preserves_literal_gaps(tmp_path):
    env = Scenario(tmp_path)
    env.seed_foundation()
    env.apply('mixed-nodes', 1, deepcopy(DATA['mixed_nodes']))
    body = {**DATA['goal_only'], 'task_requirements': deepcopy(DATA['mixed_entries'])}
    task_id = env.create(body, DATA['created_010_3'])
    env.query('task_basis', task_id, 3, DATA['mixed_basis'])
    assert env.sql('SELECT key,status,position FROM task_requirement_versions '
                   'WHERE task_id=? ORDER BY position', (task_id,)) == [tuple(row) for row in DATA['mixed_sql_rows']]


def test_obsolete_basis_error_then_entry_exclusion_and_no_active_basis(tmp_path):
    env = Scenario(tmp_path)
    env.seed_foundation()
    env.apply('extra', 1, deepcopy(DATA['extra_foundation']))
    body = {**DATA['goal_only'], 'task_requirements': DATA['statement_two']['task_requirements']}
    task_id = env.create(body, DATA['created_010_3'])
    env.apply('obsolete-application', 3, deepcopy(DATA['obsolete_application']))
    before = env.state()
    assert env.cli('query', {'queries': [
        {'id': 'basis', 'kind': 'task_basis', 'task_id': task_id},
    ]}, 2) == {'status': 'rejected',
              'reason': 'obsolete application requirement cannot plan a Task: APP-REPORT'}
    assert env.state() == before
    env.query('get_task', task_id, 4, {**DATA['empty_task'],
                                     'task_requirements': DATA['statement_two']['task_requirements']})
    statement = deepcopy(DATA['statement_two'])
    statement['task_requirements'][0]['status'] = 'obsolete'
    env.revise(task_id, statement, 4, 1)
    env.query('task_basis', task_id, 5, DATA['basis_extra'])
    statement['task_requirements'][1]['status'] = 'obsolete'
    env.revise(task_id, statement, 5, 2)
    env.query('task_basis', task_id, 6, {**DATA['basis_empty'], 'statement_revision': 3})
    assert env.sql('SELECT statement_revision,key,status FROM task_requirement_versions '
                   'WHERE task_id=? ORDER BY statement_revision,position', (task_id,)) == [tuple(row) for row in DATA['obsolete_sql_rows']]


def test_apply_and_creation_share_request_text_with_independent_replays(tmp_path):
    env = Scenario(tmp_path)
    apply_body = env.apply('same-request', 0, deepcopy(DATA['foundation']))
    creation_body = {**DATA['goal_only'], 'request_id': 'same-request'}
    task_id = env.create(creation_body, DATA['created_010_2'])
    assert env.sql('SELECT id,level FROM requirements ORDER BY id') == [
        ('APP-REPORT', 'application'), ('SYS-REPORT', 'system'),
    ]
    assert env.sql('SELECT system_id,application_id FROM system_application_versions') == [
        ('SYS-REPORT', 'APP-REPORT'),
    ]
    before = env.state()
    assert env.cli('apply', apply_body) == {'status': 'applied', 'revision': 1, 'replayed': True}
    assert env.cli('create_task', creation_body) == {**DATA['created_010_2'], 'replayed': True}
    assert env.state() == before
    env.creation(task_id, creation_body, DATA['created_010_2'], 2)
    rows = env.sql('SELECT request_id,intent,response FROM requirement_requests WHERE request_id=?',
                   ('same-request',))
    assert len(rows) == 1
    assert rows[0][0] == 'same-request'
    # Existing apply stores its exact protocol input; creation has its own owner table.
    assert json.loads(rows[0][1]) == apply_body
    assert json.loads(rows[0][2]) == {'revision': 1, 'replayed': False}
