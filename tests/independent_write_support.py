"""Test-owned SQL arrangements and overlap protocol; no subject-generated oracle."""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Event, Thread, current_thread
import unittest
from unittest.mock import patch

from poise.application.tasks import TaskCommands
from poise.infrastructure.requirements_registry import RequirementsStore
from poise.modules.foundation.errors import LockUnavailable, SQLiteBoundaryUnavailable
from poise.modules.requirements_registry.domain import RequirementsRegistry
from poise.storage import Store


FIXTURES = Path(__file__).parent


def fixture(area, name):
    return json.loads((FIXTURES / area / 'fixtures' / 'independent_writes' / name).read_text())


def rows(path, query, parameters=()):
    with closing(sqlite3.connect(path, timeout=0)) as db:
        return db.execute(query, parameters).fetchall()


def execute(path, sql, parameters=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute(sql, parameters)
        db.commit()


class PreparationGate:
    def __init__(self):
        self.entered = Event()
        self.resume = Event()
        self.calls = 0
        self.owner = None

    def pause(self):
        self.calls += 1
        self.entered.set()
        if not self.resume.wait(10):
            raise RuntimeError('ARRANGEMENT: coordinator did not release preparation')


@contextmanager
def pause_requirement_preparation(gate, request):
    original = RequirementsRegistry.apply
    selected_operations = deepcopy(request['operations'])

    def prepare(registry, operations, max_items):
        # Registry hydration also calls apply. Match the exact mutation AND
        # the designated A worker rather than a shared first requirement row.
        if current_thread() is gate.owner and operations == selected_operations:
            gate.pause()
        return original(registry, operations, max_items)

    with patch.object(RequirementsRegistry, 'apply', prepare):
        yield


def overlap(gate, prepare_a, write_b, observe_b):
    """Return B's actual outcome before A resumes, with guaranteed worker cleanup."""
    outcomes = {}

    def worker(name, operation):
        try:
            if name == 'a':
                gate.owner = current_thread()
            outcomes[name] = ('ok', operation())
        except BaseException as error:
            outcomes[name] = ('error', error)

    a = Thread(target=worker, args=('a', prepare_a), daemon=True)
    b = None
    a.start()
    try:
        if not gate.entered.wait(5):
            a.join(1)
            raise RuntimeError(f'ARRANGEMENT: A never reached preparation: {outcomes!r}')
        b = Thread(target=worker, args=('b', write_b), daemon=True)
        b.start()
        b.join(5)
        if b.is_alive():
            raise RuntimeError('WORKER-WATCHDOG: B has no terminal outcome')
        if outcomes['b'][0] == 'ok':
            observe_b()
    finally:
        gate.resume.set()
        a.join(5)
        if b is not None:
            b.join(5)
        if a.is_alive() or (b is not None and b.is_alive()):
            raise RuntimeError('WORKER-WATCHDOG: preparation worker did not stop')
    if outcomes['b'][0] == 'error':
        if outcomes['a'][0] == 'error':
            raise RuntimeError('ARRANGEMENT: A also failed after preparation was released') from outcomes['a'][1]
        error = outcomes['b'][1]
        if isinstance(error, (LockUnavailable, SQLiteBoundaryUnavailable)):
            raise AssertionError(
                'B_NOT_COMMITTED_DURING_A_PREPARATION: '
                f'{type(error).__name__}: {error}'
            ) from error
        raise RuntimeError('ARRANGEMENT: B failed outside the expected lock boundary') from error
    return outcomes['a']


def concurrent_pair(operation):
    start = Barrier(2)

    def invoke():
        start.wait(timeout=5)
        return operation()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(invoke), workers.submit(invoke)
        return first.result(timeout=10), second.result(timeout=10)


class TaskCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='poise-task-writes-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'tasks.sqlite'
        self.workflow = fixture('tasks', 'starter_workflow.json')
        self.store = Store(self.path, self.root / 'tasks.lock', 0.15, 0.002,
                           {'development': self.workflow}, read_wait=0.15)
        self.commands = TaskCommands(self.store.unit_of_work, None, None)
        self.payload = fixture('tasks', 'payload.json')
        self.newborn('B', 'actor-b')

    def newborn(self, identifier='A', actor='actor-a'):
        metadata = fixture('tasks', 'newborn_metadata.json')
        metadata['goal'] = f'Seed {identifier}.'
        metadata['newborn']['draft']['goal'] = f'Seed {identifier}.'
        self.seed_task(identifier, actor, 'newborn', metadata)

    def seed_task(self, identifier, actor, status, metadata):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,'
                       'current_submission_id,metadata) VALUES(?,?,0,1,?,0,NULL,?)',
                       (identifier, status, actor, json.dumps(metadata)))
            db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', (actor,))
            db.commit()

    def active(self):
        self.seed_task('A', 'actor-a', 'active', fixture('tasks', 'active_metadata.json'))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            for table, data in (
                ('task_workflows', fixture('tasks', 'active_workflow.json')),
                ('task_proofs', fixture('tasks', 'proof.json')),
            ):
                db.execute(f'INSERT INTO {table}(task_id,data) VALUES(?,?)', ('A', json.dumps(data)))
            db.execute('INSERT INTO content_contracts(task_id,version,data) VALUES(?,0,?)',
                       ('A', json.dumps(fixture('tasks', 'content_layers.json'))))
            db.commit()

    def edit(self, identifier='A', actor='actor-a', revision=0, request='edit-a',
             patch=None, planning_source=None):
        return self.commands.edit_newborn(
            identifier, actor, revision,
            {'goal': f'Changed {identifier}.'} if patch is None else patch,
            [], {'development': self.workflow}, 'test-config', request,
            planning_source=planning_source,
        )

    def write_b(self):
        return self.edit('B', 'actor-b', request='edit-b')

    def observe_b(self):
        self.assertEqual(rows(self.path, 'SELECT version,json_extract(metadata,?) '
                              'FROM tasks WHERE id=?', ('$.newborn.draft.goal', 'B')),
                         [(1, 'Changed B.')])

    def submit(self):
        return self.commands.submit('A', 'actor-a', deepcopy(self.payload))

    def snapshot(self):
        selections = {
            'tasks': ('id,status,claimed_by,version,current_submission_id,metadata', 'id'),
            'sessions': ('id,task_id', 'id'),
            'submissions': ('seq,task_id,stage,iteration,digest,data', 'seq'),
            'section_layers': ('submission_id,task_id,section_id,content,content_state',
                               'submission_id,section_id'),
            'task_events': ('task_id,version,data', 'seq'),
            'journal': ('session_id,task_id,event,data', 'seq'),
            'task_workflows': ('task_id,data', 'task_id'),
            'task_proofs': ('task_id,data', 'task_id'),
            'task_execution': ('task_id,version,data', 'task_id'),
            'sprints': ('id,project,state,revision,data', 'id'),
        }
        return {table: rows(self.path, f'SELECT {columns} FROM {table} ORDER BY {order}')
                for table, (columns, order) in selections.items()}


class RequirementCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='poise-requirement-writes-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'requirements.sqlite'
        self.store = RequirementsStore(self.path, self.root / 'requirements.lock', 0.15, 0.002,
                                       read_wait=0.15)
        with closing(sqlite3.connect(self.path)) as db:
            db.executemany('INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)',
                           fixture('requirements_registry', 'seed.json'))
            db.commit()
        self.request = fixture('requirements_registry', 'apply_a.json')

    def apply(self, request=None):
        return self.store.apply(deepcopy(self.request if request is None else request), 20)

    def snapshot(self):
        return {
            'requirements': rows(self.path, 'SELECT id,level,status,text FROM requirements ORDER BY id'),
            'links': rows(self.path, 'SELECT system_id,application_id FROM requirement_links '
                          'ORDER BY system_id,application_id'),
            'revision': rows(self.path, 'SELECT revision FROM requirements_meta')[0][0],
            'receipts': rows(self.path, 'SELECT request_id,digest,response FROM requirement_requests '
                             'ORDER BY request_id'),
        }

    def assert_expected(self, name):
        expected = fixture('requirements_registry', name)
        actual = self.snapshot()
        self.assertEqual(actual['requirements'], [tuple(row) for row in expected['requirements']])
        self.assertEqual(actual['links'], [tuple(row) for row in expected['links']])
        self.assertEqual(actual['revision'], expected['revision'])
        self.assertEqual(len(actual['receipts']), 1)
        self.assertEqual(json.loads(actual['receipts'][0][2]), expected['response'])
