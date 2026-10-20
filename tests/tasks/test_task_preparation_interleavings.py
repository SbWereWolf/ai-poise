"""TaskCommands: concurrent owner change and late completion of exact intent."""
from copy import deepcopy
from threading import current_thread

from independent_write_support import TaskCase, PreparationGate, overlap, rows
from poise.modules.foundation.errors import VersionConflict


class TaskIntentInterleavings(TaskCase):
    def invoke_overlap(self, write, observe):
        self.newborn()
        gate = PreparationGate()

        def starter(goal_type, task_id, sprint_id):
            if current_thread() is gate.owner:
                gate.pause()
            return {'requirements': ['Starter requirement.']}, deepcopy(self.workflow)

        def edit():
            return self.edit(patch={'goal_type': 'development'}, planning_source=starter)

        try:
            result = overlap(gate, edit, lambda: write(edit), observe)
        except AssertionError as error:
            message = str(error)
            if ('B_NOT_COMMITTED_DURING_A_PREPARATION' not in message
                    or 'LockUnavailable' not in message):
                raise RuntimeError('ARRANGEMENT: wrong command contention boundary') from error
            raise
        self.assertEqual(gate.calls, 1)
        return result

    def test_owner_change_during_preparation_rejects_candidate(self):
        def write(_):
            with self.store.transaction() as db:
                db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('new-owner',))
                db.execute('UPDATE tasks SET claimed_by=?,version=version+1 WHERE id=?',
                           ('new-owner', 'A'))

        def observe():
            self.assertEqual(rows(self.path, 'SELECT claimed_by,version FROM tasks WHERE id=?',
                                  ('A',)), [('new-owner', 1)])

        outcome = self.invoke_overlap(write, observe)
        self.assertEqual(outcome[0], 'error')
        self.assertIsInstance(outcome[1], VersionConflict)
        observe()
        self.assertEqual(rows(self.path, 'SELECT goal_type FROM '
                              '(SELECT json_extract(metadata,?) AS goal_type FROM tasks WHERE id=?)',
                              ('$.newborn.draft.goal_type', 'A')), [(None,)])
        self.assertEqual(rows(self.path, 'SELECT event FROM journal WHERE task_id=?', ('A',)), [])
        self.assertEqual(rows(self.path, 'SELECT task_id,version FROM task_events WHERE task_id=?',
                              ('A',)), [])
        self.assertEqual(rows(self.path, 'SELECT id,task_id FROM sessions ORDER BY id'),
                         [('actor-a', None), ('actor-b', None), ('new-owner', None)])

    def test_completed_same_request_during_preparation_replays_receipt(self):
        competitor = {}

        def write(edit):
            competitor['result'] = edit()

        def observe():
            self.assertEqual(rows(self.path, 'SELECT version,json_extract(metadata,?) '
                                  'FROM tasks WHERE id=?',
                                  ('$.newborn.draft.requirements', 'A')),
                             [(1, '["Starter requirement."]')])

        outcome = self.invoke_overlap(write, observe)
        if outcome[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: exact completed intent did not replay') from outcome[1]
        # Independent expected response, never generated from competitor output.
        expected = self.expected_edit_result()
        self.assertEqual(competitor['result'], expected)
        self.assertEqual(outcome[1], {**expected, 'replayed': True})
        observe()
        self.assertEqual(rows(self.path, 'SELECT event FROM journal WHERE task_id=?', ('A',)),
                         [('newborn.action',)])
        self.assertEqual(rows(self.path, 'SELECT task_id,version FROM task_events WHERE task_id=?',
                              ('A',)), [('A', 1)])

    @staticmethod
    def expected_edit_result():
        from independent_write_support import fixture
        return fixture('tasks', 'public_query_intent_result.json')
