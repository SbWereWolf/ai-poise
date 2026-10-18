"""Test-owned public-read arrangements and causal instrumentation.

Subjects are real TaskQueries, Store and SqliteAccounting on disposable SQLite.
No production serializer, factory or emitted projection supplies an oracle.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import sqlite3
from threading import current_thread
from unittest.mock import patch

from independent_write_support import TaskCase, PreparationGate, fixture, overlap, rows
from poise.infrastructure.sqlite.accounting import SqliteAccounting
from poise.modules.artifact_factory import locations
from poise.modules.result_integration.domain import IntegrationRun
from poise.modules.tasks.domain import TaskStageContracts
from poise.modules.tasks.stage_criteria import StageCriteriaBook
from poise.modules.verification.domain import CheckRegistry


def encoded(value):
    """Test-owned SQL input encoding, not the subject serializer."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


PUBLIC_KEYS = (
    'content', 'evidence', 'registry', 'record', 'current', 'accounting_snapshot',
    'accounting_nonblocking', 'criteria', 'pending_receipt', 'resolve_path',
    'trace', 'submission', 'result', 'terminal', 'history', 'summary',
    'standalone_summary',
)


class PublicQueryCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.inputs = fixture('tasks', 'public_query_input.json')
        self.expected = fixture('tasks', 'public_query_expected.json')
        self.accounting = SqliteAccounting(self.store.database, 'test-project', {})

    def arrange_public(self, key='content'):
        self.active()
        self.metadata = fixture('tasks', 'active_metadata.json')
        self.metadata['contract']['evidence_plan']['work']['arguments'] = deepcopy(
            self.inputs['evidence_plan_arguments'])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO submissions(seq,task_id,stage,iteration,digest,data) '
                       'VALUES(41,?, ?,1,?,?)',
                       ('A', 'work', 'a' * 64, encoded(self.inputs['material'])))
            db.execute('UPDATE tasks SET current_submission_id=41,metadata=? WHERE id=?',
                       (encoded(self.metadata), 'A'))
            db.execute('UPDATE content_contracts SET data=? WHERE task_id=?',
                       (encoded(fixture('tasks', 'content_layers.json')), 'A'))
            db.execute('INSERT INTO section_layers(submission_id,task_id,section_id,content,'
                       'content_state) VALUES(41,?,?,?,?)',
                       ('A', 'report', 'Report A.', 'populated'))
            db.execute('INSERT INTO task_results(task_id,submission_id,data) VALUES(?,41,?)',
                       ('A', encoded(self.inputs['result'])))
            db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,0,?,?)',
                       ('A', '2026-01-01T00:00:00+00:00', encoded(self.inputs['event'])))
            db.execute('UPDATE task_proofs SET data=? WHERE task_id=?',
                       (encoded(self.inputs['proof_old']), 'A'))
            db.execute('INSERT INTO transfer_locations(task_id,data) VALUES(?,?)',
                       ('A', encoded(self.inputs['locations_old'])))
            if key == 'trace':
                db.execute('INSERT INTO trace_point_layers(submission_id,task_id,route_id,'
                           'point_id,data) VALUES(41,?,?,?,?)',
                           ('A', 'route', 'point', encoded(self.inputs['trace'])))
            if key == 'pending_receipt':
                execution = fixture('tasks', 'execution.json')
                execution['pending'] = self.inputs['pending']
                db.execute('INSERT INTO task_execution(task_id,data,version) VALUES(?,?,0)',
                           ('A', encoded(execution)))
            if key == 'terminal':
                db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?',
                           ('completed', 'A'))
            if key.startswith('accounting_'):
                self.insert_accounting(db)
            db.commit()

    def insert_accounting(self, db):
        # Identifiers come only from this explicit test-fixture table allowlist;
        # values are parameterized and columns are quoted, never SELECT *.
        allowed = {
            'accounting_accounts', 'accounting_usage', 'accounting_cycles',
            'accounting_credits', 'interaction_events', 'interaction_bindings',
            'interaction_reports', 'accounting_findings',
        }
        for table, records in self.inputs['accounting'].items():
            assert table in allowed
            for record in records:
                columns = ','.join('"' + name.replace('"', '""') + '"' for name in record)
                placeholders = ','.join('?' for _ in record)
                db.execute(f'INSERT INTO "{table}"({columns}) VALUES({placeholders})',
                           tuple(record.values()))

    def operation(self, key):
        query = self.store.queries
        return {
            'content': lambda: query.content('A'),
            'evidence': lambda: query.evidence_view('A'),
            'registry': lambda: query.verification_registry('A'),
            'record': lambda: query.record('A'),
            'current': lambda: self.store.current('actor-a'),
            'accounting_snapshot': self.accounting.snapshot,
            'accounting_nonblocking': self.accounting.snapshot_nonblocking,
            'criteria': lambda: query.stage_criteria('A', 'work'),
            'pending_receipt': lambda: query.receipt_task('receipt-a'),
            'resolve_path': lambda: query.resolve_path('A', '/origin/report.txt'),
            'trace': lambda: query.trace_point('A', 'route', 'point', None),
            'submission': lambda: query.current_submission('A'),
            'result': lambda: query.result('A', 41),
            'terminal': lambda: query.terminal_snapshot('A'),
            'history': lambda: query.history('A'),
            'summary': query.summary,
            'standalone_summary': query.standalone_summary,
        }[key]

    def expected_public(self, key):
        expected_key = {'current': 'record', 'accounting_snapshot': 'accounting',
                        'accounting_nonblocking': 'accounting'}.get(key, key)
        return 'A' if key == 'pending_receipt' else self.expected[expected_key]

    @contextmanager
    def pause_public(self, key, gate):
        if key in ('content', 'evidence', 'registry'):
            owner, name = TaskStageContracts, 'parse'
        elif key in ('record', 'current', 'accounting_snapshot', 'accounting_nonblocking'):
            owner, name = CheckRegistry, 'restore_state'
        elif key == 'criteria':
            owner, name = StageCriteriaBook, 'from_dict'
        elif key == 'pending_receipt':
            owner, name = IntegrationRun, 'restore'
        elif key == 'resolve_path':
            owner, name = locations, 'relocate_path'
        else:
            owner, name = json, 'loads'
        original = getattr(owner, name)
        selected = {
            'trace': encoded(self.inputs['trace']),
            'submission': encoded(self.inputs['material']),
            'result': encoded(self.inputs['result']),
            'history': encoded(self.inputs['event']),
            'terminal': encoded(self.metadata),
            'summary': encoded(self.metadata),
            'standalone_summary': encoded(self.metadata),
        }.get(key)

        def pure(*args, **kwargs):
            if current_thread() is gate.owner and (selected is None or args[0] == selected):
                gate.pause()
            return original(*args, **kwargs)

        with patch.object(owner, name, pure):
            yield

    def public_overlap(self, key, gate, operation, write, observe):
        try:
            return overlap(gate, operation, write, observe)
        except AssertionError as error:
            message = str(error)
            expected = ('LockUnavailable' if key == 'accounting_snapshot'
                        else 'SQLiteBoundaryUnavailable')
            if ('B_NOT_COMMITTED_DURING_A_PREPARATION' not in message
                    or expected not in message
                    or (expected == 'SQLiteBoundaryUnavailable' and 'COMMIT' not in message)):
                raise RuntimeError('ARRANGEMENT: wrong public-read contention boundary') from error
            raise

    def assert_public_causal(self, key):
        self.arrange_public(key)
        gate = PreparationGate()
        with self.pause_public(key, gate):
            outcome = self.public_overlap(key, gate, self.operation(key),
                                          self.write_b, self.observe_b)
        if outcome[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: A failed after read preparation') from outcome[1]
        self.assertEqual(gate.calls, 1)
        self.assertEqual(outcome[1], self.expected_public(key))

    def write_related_evidence(self):
        with self.store.transaction() as db:
            db.execute('UPDATE task_proofs SET data=? WHERE task_id=?',
                       (encoded(self.inputs['proof_new']), 'A'))
            db.execute('UPDATE transfer_locations SET data=? WHERE task_id=?',
                       (encoded(self.inputs['locations_new']), 'A'))

    def observe_related_evidence(self):
        self.assertEqual(rows(self.path, 'SELECT data FROM task_proofs WHERE task_id=?',
                              ('A',)), [(encoded(self.inputs['proof_new']),)])
        self.assertEqual(rows(self.path, 'SELECT data FROM transfer_locations WHERE task_id=?',
                              ('A',)), [(encoded(self.inputs['locations_new']),)])

    def write_related_registry(self):
        metadata = deepcopy(self.metadata)
        metadata['contract']['methods'] = [self.inputs['registry_entry']['method']]
        workflow = fixture('tasks', 'active_workflow.json')
        workflow['registry'] = self.inputs['registry_new']
        with self.store.transaction() as db:
            db.execute('INSERT INTO task_methods(task_id,method_id,version,data) VALUES(?,?,0,?)',
                       ('A', 'M', encoded(self.inputs['registry_entry'])))
            db.execute('UPDATE task_workflows SET data=? WHERE task_id=?',
                       (encoded(workflow), 'A'))
            db.execute('UPDATE tasks SET metadata=?,version=version+1 WHERE id=?',
                       (encoded(metadata), 'A'))

    def observe_related_registry(self):
        self.assertEqual(rows(self.path, 'SELECT method_id,data FROM task_methods WHERE task_id=?',
                              ('A',)), [('M', encoded(self.inputs['registry_entry']))])
        state = rows(self.path, 'SELECT data FROM task_workflows WHERE task_id=?', ('A',))[0][0]
        self.assertEqual(json.loads(state)['registry'], self.inputs['registry_new'])
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(1,)])

    def write_registered_content(self):
        with self.store.transaction() as db:
            db.execute('UPDATE content_contracts SET data=? WHERE task_id=?',
                       (encoded(self.inputs['content_registered']), 'A'))
            db.execute('INSERT INTO section_layers(submission_id,task_id,section_id,content,'
                       'content_state) VALUES(41,?,?,?,?)',
                       ('A', 'analysis', 'Prepared facts.', 'populated'))
            db.execute('INSERT INTO trace_point_layers(submission_id,task_id,route_id,'
                       'point_id,data) VALUES(41,?,?,?,?)',
                       ('A', 'route', 'point', encoded('Trace facts.')))

    def make_newborn(self, *, restarted=False, typed=False):
        metadata = fixture('tasks', 'newborn_metadata.json')
        if typed:
            metadata['newborn']['draft'].update(
                goal_type='development',
                methods=[deepcopy(self.inputs['registry_entry']['method'])],
            )
            metadata['workflow'] = deepcopy(self.metadata['workflow'])
        if restarted:
            metadata['contract'] = deepcopy(self.metadata['contract'])
            metadata['newborn']['draft'] = {
                'goal': 'Seed A.', 'goal_type': 'development', 'methods': [],
            }
            metadata['workflow'] = deepcopy(self.metadata['workflow'])
            metadata['registry_workflow'] = deepcopy(self.metadata['workflow'])
            metadata['restart_history'] = [{'from_status': 'active', 'from_version': 0,
                                           'reason': 'Fixture restart.',
                                           'authorization': 'Reviewed fixture restart.'}]
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET status=?,metadata=?,current_submission_id=NULL WHERE id=?',
                       ('newborn', encoded(metadata), 'A'))
            if not restarted:
                db.execute('DELETE FROM task_workflows WHERE task_id=?', ('A',))
        return metadata
