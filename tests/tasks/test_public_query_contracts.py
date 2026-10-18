"""Existing public DTO/validation contracts, independently authored literal oracles."""
from copy import deepcopy
from threading import Thread, current_thread

from independent_write_support import PreparationGate, rows
from public_query_support import PublicQueryCase, PUBLIC_KEYS, encoded
from poise.modules.foundation.errors import DomainError, PoiseError


class PublicQueryContracts(PublicQueryCase):
    def assert_projection(self, key):
        self.arrange_public(key)
        self.assertEqual(self.operation(key)(), self.expected_public(key))

    def test_active_content_dto(self):
        self.assert_projection('content')
        query = self.store.queries
        self.assertEqual(query.section('A', 'work', 'report', 41), self.expected['section'])
        with self.subTest(state='registered_content'):
            self.write_registered_content()
            self.assertEqual(query.content('A'), self.expected['content_registered'])
            self.assertEqual(query.section('A', 'work', 'analysis', 41),
                             self.expected['analysis_section'])

    def test_active_evidence_dto(self):
        self.assert_projection('evidence')
        self.write_related_evidence()
        self.observe_related_evidence()
        self.assertEqual(self.store.queries.evidence_view('A'), self.expected['evidence_new'])

    def test_active_registry_dto(self):
        self.assert_projection('registry')
        self.write_related_registry()
        self.observe_related_registry()
        self.assertEqual(self.store.queries.verification_registry('A'), self.expected['registry_new'])

    def test_active_record_dto(self):
        self.assert_projection('record')

    def test_newborn_registry_is_inactive(self):
        self.arrange_public()
        with self.subTest(state='untyped'):
            self.make_newborn()
            self.assertEqual(self.store.queries.verification_registry('A'),
                             self.expected['newborn_registry'])
        with self.subTest(state='typed_with_draft_methods'):
            self.make_newborn(typed=True)
            self.assertEqual(self.store.queries.verification_registry('A'),
                             self.expected['typed_newborn_registry'])

    def test_restarted_registry_preserves_history(self):
        self.arrange_public()
        with self.subTest(state='empty_historical_registry'):
            self.make_newborn(restarted=True)
            self.assertEqual(self.store.queries.verification_registry('A'),
                             self.expected['restarted_registry'])
        with self.subTest(state='populated_historical_registry'):
            case = PublicQueryCase()
            case.setUp()
            try:
                case.arrange_public()
                case.write_related_registry()
                case.make_newborn(restarted=True)
                self.assertEqual(case.store.queries.verification_registry('A'),
                                 self.expected['populated_restarted_registry'])
            finally:
                case.doCleanups()

    def test_terminal_record_preserves_history(self):
        self.arrange_public('terminal')
        self.assertEqual(self.store.queries.record('A'), self.expected['terminal_record'])

    def test_terminal_ledger_preserves_history(self):
        self.assert_projection('terminal')

    def test_current_uses_claim_owner(self):
        self.assert_projection('current')
        self.assertIsNone(self.store.current('unclaimed-actor'))
        self.assertEqual(rows(self.path, 'SELECT id,task_id FROM sessions ORDER BY id'),
                         [('actor-a', None), ('actor-b', None)])

    def test_stage_criteria_dto(self):
        self.assert_projection('criteria')

    def test_pending_receipt_owner(self):
        self.assert_projection('pending_receipt')

    def test_evidence_receipt_owner(self):
        self.arrange_public()
        with self.store.transaction() as db:
            db.execute('INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,1,?)',
                       ('receipt-saved', 'A', 'work', encoded({'id': 'receipt-saved'})))
        self.assertEqual(self.store.queries.receipt_task('receipt-saved'), 'A')

    def test_missing_receipt_returns_none(self):
        self.arrange_public()
        self.assertIsNone(self.store.queries.receipt_task('absent'))

    def test_missing_task_contracts(self):
        self.arrange_public()
        query = self.store.queries
        for name in ('content', 'evidence_view', 'verification_registry'):
            with self.subTest(entry=name):
                with self.assertRaisesRegex(PoiseError, 'Задача не найдена: absent'):
                    getattr(query, name)('absent')
        with self.assertRaisesRegex(PoiseError, 'Task not found'):
            query.stage_criteria('absent', 'work')
        self.assertIsNone(query.record('absent'))
        self.assertIsNone(query.current_submission('absent'))
        with self.assertRaisesRegex(PoiseError, 'Terminal ledger requires'):
            query.terminal_snapshot('absent')
        for call, message in ((lambda: query.section('absent', 'work', 'report', None),
                               'Секция не найдена'),
                              (lambda: query.trace_point('absent', 'route', 'point', None),
                               'Точка трассировки не найдена'),
                              (lambda: query.result('absent', 41), 'Проверенный результат не найден')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(PoiseError, message):
                    call()

    def protected_state(self):
        state = self.snapshot()
        columns = {
            'content_contracts': 'task_id,version,data',
            'task_workflows': 'task_id,data',
            'task_proofs': 'task_id,data',
            'task_methods': 'task_id,method_id,version,data',
            'transfer_locations': 'task_id,data',
            'trace_point_layers': 'task_id,submission_id,route_id,point_id,data',
            'task_results': 'task_id,submission_id,data',
        }
        for table, fields in columns.items():
            state[table] = rows(self.path, f'SELECT {fields} FROM {table} ORDER BY task_id')
        return state

    def assert_restore_error(self, error_type, message, *, exact=False):
        before = self.protected_state()
        for name in ('content', 'evidence_view', 'verification_registry'):
            with self.subTest(entry=name):
                with self.assertRaisesRegex(error_type, message) as raised:
                    getattr(self.store.queries, name)('A')
                if exact:
                    self.assertIs(type(raised.exception), error_type)
                    self.assertEqual(str(raised.exception), message)
                self.assertEqual(self.protected_state(), before)

    def remove_row(self, table):
        assert table in ('content_contracts', 'task_workflows', 'task_proofs')
        with self.store.transaction() as db:
            db.execute(f'DELETE FROM {table} WHERE task_id=?', ('A',))

    def update_metadata(self, value):
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET metadata=? WHERE id=?', (encoded(value), 'A'))

    def test_missing_content_is_rejected(self):
        self.arrange_public()
        with self.subTest(state='missing_content_row'):
            self.remove_row('content_contracts')
            self.assert_restore_error(PoiseError, 'Нет зарегистрированного контракта содержимого')
        with self.subTest(state='corrupt_goal_sections'):
            with self.store.transaction() as db:
                db.execute('INSERT INTO content_contracts(task_id,version,data) VALUES(?,0,?)',
                           ('A', encoded(self.inputs['content_corrupt'])))
            self.assert_restore_error(DomainError, 'goal.sections: требуется список', exact=True)

    def test_missing_workflow_is_rejected(self):
        self.arrange_public()
        self.remove_row('task_workflows')
        self.assert_restore_error(PoiseError, 'Нет сохранённого состояния маршрута')

    def test_missing_proof_is_rejected(self):
        self.arrange_public()
        self.remove_row('task_proofs')
        self.assert_restore_error(PoiseError, 'Нет обязательного evidence state')

    def test_missing_criteria_is_rejected(self):
        self.arrange_public()
        metadata = deepcopy(self.metadata)
        del metadata['stage_criteria']
        self.update_metadata(metadata)
        self.assert_restore_error(PoiseError, 'stage criteria migration required')
        with self.assertRaisesRegex(PoiseError, 'stage criteria migration required'):
            self.store.queries.stage_criteria('A', 'work')

    def test_corrupt_workflow_is_rejected(self):
        self.arrange_public()
        from independent_write_support import fixture
        workflow = fixture('tasks', 'active_workflow.json')
        workflow['progress']['visits'] = {'absent': 1}
        with self.store.transaction() as db:
            db.execute('UPDATE task_workflows SET data=? WHERE task_id=?',
                       (encoded(workflow), 'A'))
        self.assert_restore_error(DomainError, 'Позиция Task не соответствует')

    def test_corrupt_registry_is_rejected(self):
        self.arrange_public()
        from independent_write_support import fixture
        for registry, error, message in (
            (None, PoiseError, 'Current verification registry state is missing or corrupt'),
            ({'revision': -1, 'history': [], 'requests': [], 'executable_obligations': []},
             DomainError, 'registry state.revision'),
        ):
            with self.subTest(registry=registry):
                workflow = fixture('tasks', 'active_workflow.json')
                workflow['registry'] = registry
                with self.store.transaction() as db:
                    db.execute('UPDATE task_workflows SET data=? WHERE task_id=?',
                               (encoded(workflow), 'A'))
                self.assert_restore_error(error, message)

    def test_corrupt_criteria_is_rejected(self):
        self.arrange_public()
        metadata = deepcopy(self.metadata)
        metadata['stage_criteria']['stages']['work']['revision'] = -1
        self.update_metadata(metadata)
        self.assert_restore_error(DomainError, 'Invalid stored criteria revision')
        with self.assertRaisesRegex(DomainError, 'Invalid stored criteria revision'):
            self.store.queries.stage_criteria('A', 'work')

    def test_restarted_registry_context_is_required(self):
        self.arrange_public()
        metadata = self.make_newborn(restarted=True)
        del metadata['registry_workflow']
        self.update_metadata(metadata)
        with self.assertRaisesRegex(PoiseError, 'Restarted Task has no frozen registry workflow context'):
            self.store.queries.verification_registry('A')

    def test_locations_rewrite_only_mechanical_paths(self):
        self.arrange_public()
        self.assertEqual(self.store.queries.result('A', 41), self.expected['result'])
        self.assertEqual(self.store.queries.current_submission('A'), self.expected['submission'])
        self.assertEqual(self.store.queries.resolve_path('A', '/origin/report.txt'),
                         self.expected['resolve_path'])
        self.assertEqual(self.store.queries.history('A'), self.expected['history'])

    def test_absent_location_preserves_paths(self):
        self.arrange_public()
        with self.store.transaction() as db:
            db.execute('DELETE FROM transfer_locations WHERE task_id=?', ('A',))
        self.assertEqual(self.store.queries.result('A', 41), self.expected['result_unmoved'])
        self.assertEqual(self.store.queries.resolve_path('A', '/origin/report.txt'),
                         '/origin/report.txt')

    def test_accounting_full_snapshot_dto(self):
        self.assert_projection('accounting_snapshot')

    def test_accounting_nonblocking_snapshot_dto(self):
        self.assert_projection('accounting_nonblocking')

    def test_corrupt_proof_is_rejected(self):
        self.arrange_public()
        proof = deepcopy(self.inputs['proof_old'])
        proof['book']['batches'] = 'bad'
        with self.store.transaction() as db:
            db.execute('UPDATE task_proofs SET data=? WHERE task_id=?', (encoded(proof), 'A'))
        self.assert_restore_error(DomainError, 'Evidence book collections must be explicit lists')

    def test_metadata_route_mismatch_is_rejected(self):
        self.arrange_public()
        metadata = deepcopy(self.metadata)
        metadata['contract']['stage_contracts'][0]['stage_id'] = 'absent'
        self.update_metadata(metadata)
        self.assert_restore_error(DomainError, 'stage contracts require exact route coverage')

    def test_public_hook_excludes_b_worker(self):
        for key in PUBLIC_KEYS:
            with self.subTest(entry=key):
                case = PublicQueryCase()
                case.setUp()
                try:
                    case.arrange_public(key)
                    gate = PreparationGate()
                    gate.owner = Thread()  # No A exists in this negative-control scenario.
                    with case.pause_public(key, gate):
                        case.write_b()
                    case.observe_b()
                    self.assertEqual(gate.calls, 0)
                finally:
                    case.doCleanups()
        # The competing exact request has its own positive control: baseline
        # locking must not hide a malformed starter or an incorrect full oracle.
        from independent_write_support import TaskCase, fixture
        case = TaskCase()
        case.setUp()
        try:
            case.newborn()

            def starter(goal_type, task_id, sprint_id):
                return {'requirements': ['Starter requirement.']}, deepcopy(case.workflow)

            result = case.edit(patch={'goal_type': 'development'}, planning_source=starter)
            self.assertEqual(result, fixture('tasks', 'public_query_intent_result.json'))
        finally:
            case.doCleanups()

    def test_public_hook_selects_a_once(self):
        for key in PUBLIC_KEYS:
            with self.subTest(entry=key):
                case = PublicQueryCase()
                case.setUp()
                try:
                    case.arrange_public(key)
                    gate = PreparationGate()
                    gate.owner = current_thread()
                    gate.resume.set()
                    with case.pause_public(key, gate):
                        result = case.operation(key)()
                    self.assertEqual(result, case.expected_public(key))
                    self.assertEqual(gate.calls, 1)
                finally:
                    case.doCleanups()
