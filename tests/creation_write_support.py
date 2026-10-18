"""Literal creation arrangements and real SQLite/Git owner boundaries."""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import sqlite3
import subprocess
import sys
from threading import current_thread
from unittest.mock import patch

from independent_write_support import TaskCase, PreparationGate, fixture, rows, overlap
from poise.application.planning_publication import PlanningPublications
from poise.application.sprints import SprintCommands
from poise.application.tasks import TaskCommands
from poise.infrastructure.repository_tree import GitRepositoryTree
from poise.infrastructure.requirements_registry import RequirementsStore
from poise.modules.requirements_registry.service import TaskRequirementsGate
from poise.modules.tasks.stage_criteria import StageCriteriaBook
from poise.modules.foundation.errors import LockUnavailable


class CreationCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.data = fixture('tasks', 'creation_input.json')
        self.expected = fixture('tasks', 'creation_expected.json')
        self.workflow = self.data['workflow']
        self.policy = self.data['task_ids']
        self.decomposition = self.data['decomposition_policy']
        self.repository = self.root / 'repository'
        self.repository.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repository / 'input.py').write_text('def test_input():\n    assert 2 + 2 == 4\n')
        self.git('add', 'input.py')
        self.git('commit', '-m', 'Creation input')
        self.base = self.git('rev-parse', 'HEAD')
        self.tree = GitRepositoryTree(self.repository, 5, 1000)
        req = RequirementsStore(self.root / 'requirements.sqlite',
                                self.root / 'requirements.lock', .15, .002, read_wait=.15)
        with closing(sqlite3.connect(req.database)) as db:
            snapshot = self.data['contract']['requirements_snapshot']
            db.executemany('INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)',
                           [(v['id'], v['level'], v['status'], v['text'])
                            for v in snapshot['requirements'].values()])
            db.execute('INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)',
                       ('SYS', 'APP'))
            db.commit()
        self.requirements = req
        self.commands = TaskCommands(self.store.unit_of_work, self.tree,
                                    TaskRequirementsGate.enabled(req.registry))
        with closing(sqlite3.connect(self.path)) as db:
            db.executemany('INSERT INTO sessions(id,task_id) VALUES(?,NULL)',
                           [('actor-a',), ('actor-c',)])
            db.commit()

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.repository), *arguments],
                                       text=True, stderr=subprocess.PIPE).strip()

    def intent(self, request='create-a', *, identifier=None, sprint=None,
               goal='First child result.'):
        body = deepcopy(self.data['contract'])
        body['methods'][0]['argv'][0] = sys.executable
        body.update(sprint_id=sprint, goal=goal)
        if identifier is not None:
            body['id'] = identifier
            return body
        del body['id']
        return {'request_id': request, 'task': body}

    def create(self, intent=None, actor='actor-a'):
        return self.commands.create(
            self.intent() if intent is None else deepcopy(intent), actor, self.workflow, [],
            {'config_hash': 'test-config'}, None, self.policy, self.base, self.decomposition)

    def raw_seed(self, identifier, status, metadata, *, actor=None, stage=0, version=0):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,'
                       'current_submission_id,metadata) VALUES(?,?,?,1,?,?,NULL,?)',
                       (identifier, status, stage, actor, version, json.dumps(metadata)))
            db.commit()

    def children(self, kind='tasks'):
        return [self.intent('child-a', sprint=None if kind == 'tasks' else 'S'),
                self.intent('child-b', sprint=None if kind == 'tasks' else 'S',
                            goal='Second child result.')]

    def plan_body(self, kind, children=None):
        children = self.children(kind) if children is None else deepcopy(children)
        if kind == 'tasks':
            return children
        body = deepcopy(self.data['reviewed_sprint_body'])
        body['changes'][2]['tasks'] = children
        return body

    def seed_parent(self, kind='tasks', children=None):
        metadata = deepcopy(self.data['parent_metadata'])
        self.raw_seed('PLAN', 'active', metadata, actor='actor-a', stage=2)
        workflow = fixture('tasks', 'active_workflow.json')
        workflow['progress']['visits'] = {'draft': 1, 'review': 1, 'publish': 1}
        workflow['progress']['transitions'] = 2
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO task_workflows(task_id,data) VALUES(?,?)',
                       ('PLAN', json.dumps(workflow)))
            db.execute('INSERT INTO task_proofs(task_id,data) VALUES(?,?)',
                       ('PLAN', json.dumps(fixture('tasks', 'proof.json'))))
            db.execute('INSERT INTO content_contracts(task_id,version,data) VALUES(?,0,?)',
                       ('PLAN', json.dumps(fixture('tasks', 'content_layers.json'))))
            db.execute('INSERT INTO submissions(seq,task_id,stage,iteration,digest,data) '
                       'VALUES(41,?,?,1,?,?)', ('PLAN', 'draft', 'a' * 64, '{}'))
            db.execute('INSERT INTO section_layers(submission_id,task_id,section_id,content,'
                       'content_state) VALUES(41,?,?,?,?)',
                       ('PLAN', 'planned_tasks', json.dumps(self.plan_body(kind, children)), 'populated'))
            db.commit()

    def publisher(self):
        return PlanningPublications(
            self.store.unit_of_work, 'fixture', {'development': self.workflow}, [],
            self.data['sprint_policy'], self.policy, self.decomposition, 'test-config', 20,
            self.commands.prepare_creation, self.creation_base)

    def creation_base(self):
        return self.base

    def publication(self, kind='tasks'):
        return self.publisher().publish('PLAN', 'actor-a',
                                        deepcopy(self.expected['publication_intents'][kind]))

    def seed_sprint(self):
        for identifier, goal in [('0001', 'First child result.'), ('0002', 'Second child result.')]:
            contract = self.intent(identifier=identifier, sprint='S', goal=goal)
            draft = {k: v for k, v in contract.items() if k not in ('id', 'sprint_id')}
            self.raw_seed(identifier, 'newborn', {
                'sprint_id': 'S', 'goal': goal, 'config_hash': 'test-config',
                'workflow': self.workflow, 'newborn': {'draft': draft, 'ready': True},
                'requirements_snapshot': draft['requirements_snapshot'],
                'requirements_agreement': draft['requirements_agreement'],
            })
        aggregate = {'plan': deepcopy(self.data['sprint_plan']),
                     'policy': self.data['sprint_policy'], 'revision': 1, 'state': 'draft',
                     'waivers': [], 'decisions': []}
        record = {'project': 'fixture', 'actor': 'actor-a', 'aggregate': aggregate,
                  'workflows': {'development': self.workflow}, 'automatic_checks': [],
                  'task_decomposition': self.decomposition, 'execution_hash': 'test-config'}
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO sprints(id,project,state,revision,data) VALUES(?,?,?,1,?)',
                       ('S', 'fixture', 'draft', json.dumps(record)))
            db.executemany('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)',
                           [('S', '0001'), ('S', '0002')])
            db.execute('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) '
                       'VALUES(?,?,?,?)', ('S', '0001', '0002', 'completion'))
            db.commit()

    def sprint_publication(self):
        commands = SprintCommands(
            self.store.unit_of_work, 'fixture', 'actor-a', self.data['sprint_policy'],
            self.policy, {'development': self.workflow}, [], self.decomposition, 'test-config',
            self.commands.prepare_creation, self.creation_base, self.commands.prepare_newborn_ready,
            self.commands.promote_newborn_ready_in, lambda: None, lambda: None)
        return commands.apply({'action': 'publish', 'sprint_id': 'S',
                               'request_id': 'publish-s', 'expected_revision': 1})

    @contextmanager
    def instrumentation(self, gate, identifiers=('0001',)):
        original = StageCriteriaBook.initialize_initial
        original_tree = self.tree.existing_paths
        original_prepare = self.commands.prepare_creation
        original_base = self.creation_base
        counts = {'git': 0, 'prepare': 0, 'base': 0, 'builds': [], 'other_builds': []}

        def initialize(cls, route, contracts, contract, snapshot):
            if current_thread() is not gate.owner and contract['id'] in identifiers:
                counts['other_builds'].append(contract['id'])
            if current_thread() is gate.owner and contract['id'] in identifiers:
                counts['builds'].append(contract['id'])
                if not gate.entered.is_set():
                    gate.pause()
            return original(route, contracts, contract, snapshot)

        def tree(revision, paths):
            if current_thread() is gate.owner:
                if not paths:
                    raise RuntimeError('ARRANGEMENT: Git preflight needs nonempty input')
                counts['git'] += 1
            return original_tree(revision, paths)

        def prepare(*args, **kwargs):
            if current_thread() is gate.owner:
                counts['prepare'] += 1
            return original_prepare(*args, **kwargs)

        def base():
            if current_thread() is gate.owner:
                counts['base'] += 1
            return original_base()

        with patch.object(StageCriteriaBook, 'initialize_initial', classmethod(initialize)), \
                patch.object(self.tree, 'existing_paths', tree), \
                patch.object(self.commands, 'prepare_creation', prepare), \
                patch.object(self, 'creation_base', base):
            yield counts

    def creation_overlap(self, operation, write=None, observe=None):
        gate = PreparationGate()
        with self.instrumentation(gate, ('0001', '0002')) as counts:
            try:
                result = overlap(gate, operation, self.write_b if write is None else write,
                                 self.observe_b if observe is None else observe)
            except AssertionError as error:
                if not isinstance(error.__cause__, LockUnavailable):
                    raise RuntimeError('ARRANGEMENT: wrong creation lock boundary') from error
                raise
        self.assertEqual(gate.calls, 1)
        if result[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: A creation failed after release') from result[1]
        return result[1], counts

    def persisted_children(self, identifiers=None):
        where = ('id NOT IN (?,?)' if identifiers is None else
                 'id IN (' + ','.join('?' for _ in identifiers) + ')')
        selected = ('B', 'PLAN') if identifiers is None else tuple(identifiers)
        return rows(self.path, 'SELECT id,status,claimed_by,version,'
                    'json_extract(metadata,?),json_extract(metadata,?),stage_index,iteration '
                    f'FROM tasks WHERE {where} ORDER BY id',
                    ('$.goal', '$.creation_request.request_id', *selected))

    def assert_children(self, scenario, *, sprint_id, identifiers=None):
        expected = [tuple(row) for row in self.expected['rows'][scenario]]
        self.assertEqual(self.persisted_children(identifiers), expected)
        for identifier, _, _, version, goal, _, _, _ in expected:
            self.assertEqual(rows(self.path, 'SELECT task_id FROM task_workflows WHERE task_id=?',
                                  (identifier,)), [(identifier,)])
            self.assertEqual(rows(self.path, 'SELECT task_id FROM task_proofs WHERE task_id=?',
                                  (identifier,)), [(identifier,)])
            metadata = json.loads(rows(self.path, 'SELECT metadata FROM tasks WHERE id=?',
                                       (identifier,))[0][0])
            self.assertIn('sprint_id', metadata)
            self.assertEqual(metadata['sprint_id'], sprint_id)
            self.assertEqual(metadata['requirements_snapshot'],
                             self.data['contract']['requirements_snapshot'])
            self.assertEqual(metadata['requirements_agreement'],
                             self.data['contract']['requirements_agreement'])
            self.assertEqual(metadata['stage_criteria']['stages']['work']['criteria'],
                             self.data['contract']['stage_contracts'][0]['criteria'])
            contract = deepcopy(self.expected['child_contract'])
            contract.update(id=identifier, goal=goal, sprint_id=sprint_id)
            contract['methods'][0]['argv'][0] = sys.executable
            self.assertEqual(metadata['contract'], contract)
            methods = rows(self.path, 'SELECT method_id,version,data FROM task_methods '
                           'WHERE task_id=? ORDER BY method_id', (identifier,))
            self.assertEqual([(method, revision) for method, revision, _ in methods], [('M', version)])
            self.assertEqual(json.loads(methods[0][2]), {
                'method': contract['methods'][0], 'stages': ['work']})
            self.assertEqual(json.loads(rows(self.path, 'SELECT data FROM task_proofs '
                                             'WHERE task_id=?', (identifier,))[0][0]),
                             fixture('tasks', 'proof.json'))
            self.assertEqual(json.loads(rows(self.path, 'SELECT data FROM content_contracts '
                                             'WHERE task_id=? ORDER BY version DESC LIMIT 1',
                                             (identifier,))[0][0]),
                             fixture('tasks', 'content_layers.json'))

    def expected_contract(self, identifier, goal, sprint='S'):
        contract = deepcopy(self.expected['child_contract'])
        contract.update(id=identifier, goal=goal, sprint_id=sprint)
        contract['methods'][0]['argv'][0] = sys.executable
        return contract

    def assert_publication(self, result, kind):
        expected = deepcopy(self.expected['publication'])
        expected['plan'] = {'kind': 'data_publish', 'body': self.plan_body(kind),
                            'intent': self.expected['publication_intents'][kind]}
        if kind == 'sprint':
            expected['steps'][0]['result']['sprint_id'] = 'S'
        self.assertEqual(result.to_dict(), expected)
        self.assert_children('publication', sprint_id='S' if kind == 'sprint' else None)
        self.assertEqual(rows(self.path, 'SELECT task_id,stage,iteration,version FROM action_runs'),
                         [('PLAN', 'publish', 1, 2)])
        self.assertEqual(rows(self.path, 'SELECT task_id,version FROM action_events ORDER BY version'),
                         [('PLAN', 0), ('PLAN', 1), ('PLAN', 2)])
        self.assertEqual(rows(self.path, 'SELECT status,version,claimed_by,stage_index,iteration '
                              'FROM tasks WHERE id=?', ('PLAN',)),
                         [('active', 0, 'actor-a', 2, 1)])
        if kind == 'sprint':
            record = json.loads(rows(self.path, 'SELECT data FROM sprints WHERE id=?', ('S',))[0][0])
            expected_plan = deepcopy(self.data['sprint_plan'])
            expected_plan['tasks'] = [self.expected_contract('0001', 'First child result.'),
                                      self.expected_contract('0002', 'Second child result.')]
            self.assertEqual(record['aggregate']['plan'], expected_plan)
            self.assertEqual((record['aggregate']['state'], record['aggregate']['revision']),
                             ('published', 3))
            self.assert_members()
        else:
            self.assertEqual(rows(self.path, 'SELECT id FROM sprints'), [])

    def assert_members(self):
        self.assertEqual(rows(self.path, 'SELECT sprint_id,task_id FROM sprint_members ORDER BY task_id'),
                         [('S', '0001'), ('S', '0002')])
        self.assertEqual(rows(self.path, 'SELECT sprint_id,predecessor,successor,kind '
                              'FROM sprint_dependencies'), [('S', '0001', '0002', 'completion')])

    def assert_sprint(self, result):
        self.assertEqual(result, self.expected['sprint'])
        self.assert_children('promotion', sprint_id='S')
        self.assert_members()
        self.assertEqual(rows(self.path, 'SELECT id,state,revision FROM sprints'), [('S', 'published', 2)])
        self.assertEqual(rows(self.path, 'SELECT sprint_id,request_id FROM sprint_requests'),
                         [('S', 'publish-s')])

    def owned_snapshot(self):
        snapshot = self.snapshot()
        for table, columns, order in [
            ('tasks', 'id,status,stage_index,iteration,claimed_by,version,'
                      'current_submission_id,metadata', 'id'),
            ('content_contracts', 'task_id,version,data', 'task_id,version'),
            ('task_methods', 'task_id,method_id,version,data', 'task_id,method_id'),
            ('action_runs', 'task_id,stage,iteration,version,data', 'task_id,stage,iteration'),
            ('action_events', 'task_id,stage,iteration,version,at,data', 'seq'),
            ('sprint_layers', 'sprint_id,revision,data,at', 'sprint_id,revision'),
            ('sprint_members', 'sprint_id,task_id', 'sprint_id,task_id'),
            ('sprint_dependencies', 'sprint_id,predecessor,successor,kind', 'sprint_id,predecessor,successor'),
            ('sprint_requests', 'sprint_id,request_id,digest,data', 'sprint_id,request_id'),
            ('session_sprints', 'session_id,sprint_id', 'session_id'),
        ]:
            snapshot[table] = rows(self.path, f'SELECT {columns} FROM {table} ORDER BY {order}')
        return snapshot

    def trigger(self, statement):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(statement)
            db.commit()

    def drop_trigger(self):
        self.trigger('DROP TRIGGER fail_creation')
