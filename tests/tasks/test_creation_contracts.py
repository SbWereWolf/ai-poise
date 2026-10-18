"""Separate GREEN invariants for real creation and batch publication."""
from copy import deepcopy
import json
import sqlite3
from threading import current_thread

from creation_write_support import CreationCase
from independent_write_support import PreparationGate, rows
from poise.modules.foundation.errors import PoiseError


class CreationContracts(CreationCase):
    def test_hook_selection(self):
        for kind in ('direct', 'tasks', 'sprint', 'promotion'):
            for owner in (False, True):
                with self.subTest(kind=kind, selected_a=owner):
                    case = CreationCase()
                    case.setUp()
                    self.addCleanup(case.doCleanups)
                    if kind in ('tasks', 'sprint'):
                        case.seed_parent(kind)
                        operation = lambda: case.publication(kind)
                    elif kind == 'promotion':
                        case.seed_sprint()
                        operation = case.sprint_publication
                    else:
                        operation = case.create
                    gate = PreparationGate()
                    gate.resume.set()
                    gate.owner = current_thread() if owner else None
                    with case.instrumentation(gate, ('0001', '0002')) as counts:
                        result = operation()
                        self.assertEqual(gate.calls, 1 if owner else 0)
                        builds = ['0001'] if kind == 'direct' else ['0001', '0002']
                        self.assertEqual(counts['builds' if owner else 'other_builds'], builds)
                        self.assertEqual((counts['git'], counts['prepare']),
                                         ((1, 1) if kind == 'direct' else (2, 2))
                                         if owner else (0, 0))
                        self.assertEqual(counts['base'], 1 if owner and kind != 'direct' else 0)
                        if kind == 'direct':
                            self.assertEqual(result.receipt(), {
                                'task_id': '0001', 'request_id': 'create-a', 'replayed': False})
                            case.assert_children('direct')
                            # A second genuine build cannot arm the first-call gate again.
                            if owner:
                                case.create(case.intent('next-child', goal='Second child result.'))
                                self.assertEqual(gate.calls, 1)
                                self.assertEqual(counts['builds'], ['0001', '0002'])
                        elif kind == 'promotion':
                            case.assert_sprint(result)
                        else:
                            case.assert_publication(result, kind)

    def assert_late_failure(self, operation, trigger):
        before = self.owned_snapshot()
        self.trigger(trigger)
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            operation()
        self.assertEqual(str(caught.exception), 'injected creation failure')
        self.assertEqual(self.owned_snapshot(), before)
        self.drop_trigger()

    def test_direct_creation_rolls_back_all_owned_rows(self):
        self.assert_late_failure(self.create,
            "CREATE TRIGGER fail_creation BEFORE INSERT ON task_proofs "
            "WHEN NEW.task_id='0001' AND EXISTS(SELECT 1 FROM tasks WHERE id='0001') "
            "AND EXISTS(SELECT 1 FROM task_workflows WHERE task_id='0001') "
            "AND EXISTS(SELECT 1 FROM content_contracts WHERE task_id='0001') "
            "BEGIN SELECT RAISE(ABORT,'injected creation failure'); END")
        self.assertEqual(self.create().receipt(), {
            'request_id': 'create-a', 'task_id': '0001', 'replayed': False})
        self.assert_children([
            ('0001', 'available', None, 1, 'First child result.', 'create-a')])
        before = self.owned_snapshot()
        self.assertEqual(self.create().receipt(), {
            'request_id': 'create-a', 'task_id': '0001', 'replayed': True})
        self.assertEqual(self.owned_snapshot(), before)
        self.assertEqual(self.create(self.intent('next')).task_id, '0002')

    def publication_failure(self, kind):
        self.seed_parent(kind)
        self.assert_late_failure(lambda: self.publication(kind),
            "CREATE TRIGGER fail_creation BEFORE INSERT ON task_proofs "
            "WHEN NEW.task_id='0002' AND EXISTS(SELECT 1 FROM task_proofs WHERE task_id='0001') "
            "AND EXISTS(SELECT 1 FROM tasks WHERE id='0001' AND status='available') "
            "BEGIN SELECT RAISE(ABORT,'injected creation failure'); END")
        self.assertEqual(self.persisted_children(), [])
        self.assert_publication(self.publication(kind), kind)
        before = self.owned_snapshot()
        self.assert_publication(self.publication(kind), kind)
        self.assertEqual(self.owned_snapshot(), before)

    def test_reviewed_tasks_rollback_retry_and_replay(self):
        self.publication_failure('tasks')

    def test_reviewed_sprint_rollback_retry_and_replay(self):
        self.publication_failure('sprint')

    def test_sprint_promotion_rollback_retry_and_replay(self):
        self.seed_sprint()
        self.assert_late_failure(self.sprint_publication,
            "CREATE TRIGGER fail_creation BEFORE UPDATE OF status ON tasks "
            "WHEN NEW.id='0002' AND NEW.status='available' "
            "AND EXISTS(SELECT 1 FROM tasks WHERE id='0001' AND status='available') "
            "AND EXISTS(SELECT 1 FROM task_proofs WHERE task_id='0001') "
            "BEGIN SELECT RAISE(ABORT,'injected creation failure'); END")
        self.assertEqual(rows(self.path, 'SELECT id,status,version FROM tasks WHERE id!=? ORDER BY id',
                              ('B',)), [('0001', 'newborn', 0), ('0002', 'newborn', 0)])
        self.assert_sprint(self.sprint_publication())
        before = self.owned_snapshot()
        self.assert_sprint(self.sprint_publication())
        self.assertEqual(self.owned_snapshot(), before)

    def test_completed_creation_replay_skips_external_preparation(self):
        gate = PreparationGate()
        gate.owner = current_thread()
        gate.resume.set()
        with self.instrumentation(gate) as counts:
            self.create()
            self.assertEqual((counts['git'], counts['prepare']), (1, 1))
            before = self.owned_snapshot()
            self.assertEqual(self.create(actor='actor-c').receipt(), {
                'request_id': 'create-a', 'task_id': '0001', 'replayed': True})
            self.assertEqual((counts['git'], counts['prepare']), (1, 1))
            self.assertEqual(self.owned_snapshot(), before)
        self.assertEqual(self.create(self.intent('next')).task_id, '0002')

    def test_creation_digest_conflict_is_atomic(self):
        self.create()
        before = self.owned_snapshot()
        with self.assertRaises(PoiseError) as caught:
            self.create(self.intent(goal='Changed immutable goal.'))
        self.assertEqual(str(caught.exception), self.expected['errors']['digest'])
        self.assertEqual(self.owned_snapshot(), before)
        self.assertEqual(self.create(self.intent('next')).task_id, '0002')

    def test_exhaustion_preserves_all_state(self):
        self.policy['namespace']['maximum'] = 2
        self.newborn('0001', 'occupied-one')
        self.newborn('0002', 'occupied-two')
        before = self.owned_snapshot()
        with self.assertRaises(PoiseError) as caught:
            self.create()
        self.assertEqual(str(caught.exception), self.expected['errors']['exhaustion'])
        self.assertEqual(self.owned_snapshot(), before)

    def test_preflight_failure_preserves_all_state(self):
        (self.repository / 'input.py').unlink()
        self.git('add', '-u')
        self.git('commit', '-m', 'Missing declared input')
        self.base = self.git('rev-parse', 'HEAD')
        before = self.owned_snapshot()
        with self.assertRaises(PoiseError) as caught:
            self.create()
        self.assertEqual(str(caught.exception), self.expected['errors']['preflight'])
        self.assertEqual(self.owned_snapshot(), before)
        (self.repository / 'input.py').write_text('def test_input():\n    assert 2 + 2 == 4\n')
        self.git('add', 'input.py')
        self.git('commit', '-m', 'Restore declared input')
        self.base = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.create().receipt(), {
            'request_id': 'create-a', 'task_id': '0001', 'replayed': False})

    def test_batch_reservations_and_dependencies_are_consistent(self):
        self.newborn('0001', 'occupied-one')
        children = self.children('sprint')
        children.append(self.intent(identifier='0002', sprint='S', goal='Explicit child result.'))
        self.seed_parent('sprint', children)
        result = self.publication('sprint').to_dict()
        expected = deepcopy(self.expected['publication'])
        expected['plan'] = {'kind': 'data_publish', 'body': self.plan_body('sprint', children),
                            'intent': self.expected['publication_intents']['sprint']}
        expected['steps'][0]['result'].update(
            allocations=self.expected['batch_allocations'], task_ids=['0002', '0003', '0004'],
            sprint_id='S')
        self.assertEqual(result, expected)
        receipt = result['steps'][0]['result']
        self.assertEqual(receipt['allocations'], self.expected['batch_allocations'])
        self.assertEqual(receipt['task_ids'], ['0002', '0003', '0004'])
        record = json.loads(rows(self.path, 'SELECT data FROM sprints WHERE id=?', ('S',))[0][0])
        expected_plan = deepcopy(self.data['sprint_plan'])
        expected_plan['tasks'] = [
            self.expected_contract('0002', 'Explicit child result.'),
            self.expected_contract('0003', 'First child result.'),
            self.expected_contract('0004', 'Second child result.')]
        expected_plan['dependencies'] = [
            {'predecessor': '0003', 'successor': '0004', 'kind': 'completion'}]
        self.assertEqual(record['aggregate']['plan'], expected_plan)
        self.assertEqual(rows(self.path, 'SELECT id,json_extract(metadata,?) FROM tasks '
                              'WHERE id IN (?,?,?) ORDER BY id',
                              ('$.creation_request.request_id', '0002', '0003', '0004')),
                         [('0002', None), ('0003', 'child-a'), ('0004', 'child-b')])
