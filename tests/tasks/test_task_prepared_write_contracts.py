"""Integration subject: preserved Task state, ownership, receipts and atomic writes."""
from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch

from independent_write_support import TaskCase, concurrent_pair, execute, fixture, rows
from poise.modules.foundation.errors import DomainError, PoiseError, VersionConflict
from poise.modules.tasks.domain import Task


class TaskWriteContracts(TaskCase):
    def test_concurrent_exact_intents_keep_one_receipt_and_version_step(self):
        self.newborn()
        results = concurrent_pair(self.edit)
        self.assertEqual(sorted(item.get('replayed', False) for item in results), [False, True])
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(1,)])
        self.assertEqual(rows(self.path, 'SELECT count(*) FROM journal WHERE task_id=? '
                              'AND event=?', ('A', 'newborn.action')), [(1,)])
        self.assertEqual(rows(self.path, 'SELECT count(*) FROM task_events WHERE task_id=?',
                              ('A',)), [(1,)])

    def test_newborn_success_and_exact_replay_preserve_one_effect(self):
        self.newborn()
        first = self.edit()
        before = self.snapshot()
        repeated = self.edit()
        self.assertEqual(first['revision'], 1)
        self.assertTrue(repeated['replayed'])
        self.assertEqual({k: v for k, v in repeated.items() if k != 'replayed'}, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(1,)])
        self.assertEqual(rows(self.path, 'SELECT event FROM journal WHERE task_id=?', ('A',)),
                         [('newborn.action',)])

    def test_changed_intent_is_rejected_without_altering_accepted_result(self):
        self.newborn()
        self.edit()
        before = self.snapshot()
        with self.assertRaises(PoiseError):
            self.edit(patch={'goal': 'Different intent.'})
        self.assertEqual(self.snapshot(), before)

    def test_stale_version_has_no_effect(self):
        self.newborn()
        self.edit()
        before = self.snapshot()
        with self.assertRaises(VersionConflict):
            self.edit(request='different-request')
        self.assertEqual(self.snapshot(), before)

    def test_foreign_owner_cannot_edit(self):
        self.newborn()
        before = self.snapshot()
        with self.assertRaises(DomainError):
            self.edit(actor='intruder')
        self.assertEqual(self.snapshot(), before)

    def capture_ready_guard(self):
        # Capturing the input is the first half of the real existing guard protocol;
        # no repository constructs seed rows or expected answers.
        with self.store.unit_of_work() as uow:
            newborn = uow.tasks.load_newborn('A')
            return {'snapshot': newborn,
                    'sprint_snapshot': None if newborn.sprint_id is None else uow.sprints.get(newborn.sprint_id),
                    'execution_snapshot': uow.execution.load('A') if uow.execution.exists('A') else None}

    def require_ready_guard(self, candidate):
        with self.store.unit_of_work() as uow:
            self.commands.require_prepared_newborn_in(uow, candidate)

    def test_prepared_guard_accepts_unrelated_task_update(self):
        self.newborn()
        candidate = self.capture_ready_guard()
        self.write_b()
        before = self.snapshot()
        self.require_ready_guard(candidate)
        self.assertEqual(self.snapshot(), before)

    def test_prepared_guard_rejects_changed_owner(self):
        self.newborn()
        candidate = self.capture_ready_guard()
        execute(self.path, 'UPDATE tasks SET claimed_by=?,version=version+1 WHERE id=?',
                ('new-owner', 'A'))
        before = self.snapshot()
        with self.assertRaises(VersionConflict):
            self.require_ready_guard(candidate)
        self.assertEqual(self.snapshot(), before)

    def test_prepared_guard_rechecks_execution_without_task_version_change(self):
        self.newborn()
        candidate = self.capture_ready_guard()
        execute(self.path, 'INSERT INTO task_execution(task_id,data,version) VALUES(?,?,0)',
                ('A', json.dumps(fixture('tasks', 'execution.json'))))
        before = self.snapshot()
        with self.assertRaises(VersionConflict):
            self.require_ready_guard(candidate)
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(0,)])
        self.assertEqual(self.snapshot(), before)

    def test_prepared_guard_rechecks_sprint_without_task_version_change(self):
        self.newborn()
        sprint = fixture('tasks', 'sprint.json')
        execute(self.path, 'INSERT INTO sprints(id,project,state,revision,data) VALUES(?,?,?,0,?)',
                ('S', 'test-project', 'draft', json.dumps(sprint)))
        metadata = fixture('tasks', 'newborn_metadata.json')
        metadata['sprint_id'] = 'S'
        execute(self.path, 'UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(metadata), 'A'))
        candidate = self.capture_ready_guard()
        sprint['aggregate']['revision'] = 1
        execute(self.path, 'UPDATE sprints SET revision=1,data=? WHERE id=?',
                (json.dumps(sprint), 'S'))
        before = self.snapshot()
        with self.assertRaises(VersionConflict):
            self.require_ready_guard(candidate)
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(0,)])
        self.assertEqual(self.snapshot(), before)

    def test_missing_task_and_incorrect_revision_type_have_no_effect(self):
        self.newborn()
        before = self.snapshot()
        for options in ({'identifier': 'missing'}, {'revision': '0'}):
            with self.subTest(options=options), self.assertRaises(PoiseError):
                self.edit(**options)
            self.assertEqual(self.snapshot(), before)

    def test_preparation_failure_is_not_replayed(self):
        self.active()
        before = self.snapshot()
        failure = RuntimeError('test-owned preparation fault')
        with patch.object(Task, 'submit', side_effect=failure) as prepare:
            with self.assertRaises(RuntimeError) as caught:
                self.submit()
            self.assertIs(caught.exception, failure)
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.snapshot(), before)

    def test_newborn_mid_write_failure_rolls_back_event_and_receipt(self):
        self.newborn()
        before = self.snapshot()
        execute(self.path, "CREATE TRIGGER fail_receipt BEFORE INSERT ON journal "
                "WHEN NEW.event='newborn.action' BEGIN SELECT RAISE(ABORT,'test receipt fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.edit()
        self.assertEqual(self.snapshot(), before)
        execute(self.path, 'DROP TRIGGER fail_receipt')
        self.edit()
        after = self.snapshot()
        self.edit()
        self.assertEqual(self.snapshot(), after)

    def test_submission_mid_write_failure_rolls_back_every_related_row(self):
        self.active()
        before = self.snapshot()
        execute(self.path, "CREATE TRIGGER fail_section BEFORE INSERT ON section_layers "
                "BEGIN SELECT RAISE(ABORT,'test section fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit()
        self.assertEqual(self.snapshot(), before)
        execute(self.path, 'DROP TRIGGER fail_section')
        self.submit()
        self.assert_saved_submission()

    def assert_saved_submission(self):
        expected = fixture('tasks', 'expected_submit.json')
        actual = rows(self.path, 'SELECT status,version,current_submission_id FROM tasks WHERE id=?',
                      ('A',))
        self.assertEqual(actual, [tuple(expected['state'])])
        self.assertEqual(rows(self.path, 'SELECT section_id,content,content_state FROM section_layers '
                              'WHERE task_id=? ORDER BY section_id', ('A',)),
                         [tuple(row) for row in expected['sections']])
        events = rows(self.path, 'SELECT data FROM task_events WHERE task_id=? ORDER BY seq', ('A',))
        self.assertEqual([json.loads(row[0])['event'] for row in events], expected['events'])
        self.assertEqual(rows(self.path, 'PRAGMA integrity_check'), [('ok',)])
        self.assertEqual(rows(self.path, 'PRAGMA foreign_key_check'), [])

    def test_submission_success_preserves_exact_state_and_matching_identity(self):
        self.active()
        self.submit()
        self.assert_saved_submission()
        before = self.snapshot()
        self.assertEqual(self.commands.matching_submission_digest('A', 'actor-a',
                         deepcopy(self.payload)),
                         rows(self.path, 'SELECT digest FROM submissions WHERE seq=?', (1,))[0][0])
        self.assertEqual(self.snapshot(), before)

    def test_invalid_report_and_wrong_task_status_have_no_effect(self):
        self.active()
        before = self.snapshot()
        invalid = deepcopy(self.payload)
        invalid['sections'] = {'report': ''}
        with self.assertRaises(PoiseError):
            self.commands.submit('A', 'actor-a', invalid)
        self.assertEqual(self.snapshot(), before)
        execute(self.path, 'UPDATE tasks SET status=? WHERE id=?', ('available', 'A'))
        before = self.snapshot()
        with self.assertRaises(PoiseError):
            self.submit()
        self.assertEqual(self.snapshot(), before)
