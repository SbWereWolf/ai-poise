"""Integration subject: TaskCommands preparation and real independent SQLite commits."""
from copy import deepcopy
from unittest.mock import patch

from independent_write_support import TaskCase, PreparationGate, overlap, rows
from poise.modules.tasks.domain import Task, TaskStageContracts


class IndependentTaskPreparation(TaskCase):
    def assert_a_succeeded(self, outcome):
        if outcome[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: A did not complete valid preparation') from outcome[1]

    def test_newborn_starter_preparation_allows_independent_commit(self):
        self.newborn()
        gate = PreparationGate()

        def starter(goal_type, task_id, sprint_id):
            gate.pause()
            return {'requirements': ['Starter requirement.']}, deepcopy(self.workflow)

        outcome = overlap(gate, lambda: self.edit(patch={'goal_type': 'development'},
                                                planning_source=starter),
                          self.write_b, self.observe_b)
        self.assert_a_succeeded(outcome)
        self.assertEqual(outcome[1]['revision'], 1)
        self.assertEqual(outcome[1]['draft']['requirements'], ['Starter requirement.'])

    def prepared_call(self, operation):
        self.active()
        gate = PreparationGate()
        original = Task.submit

        def prepare(task, *args, **kwargs):
            if task.state.task_id == 'A':
                gate.pause()
            return original(task, *args, **kwargs)

        with patch.object(Task, 'submit', prepare):
            outcome = overlap(gate, operation, self.write_b, self.observe_b)
        self.assert_a_succeeded(outcome)
        self.assertEqual(gate.calls, 1)
        return outcome[1]

    def test_submit_preparation_allows_independent_commit(self):
        self.prepared_call(self.submit)
        self.assertEqual(rows(self.path, 'SELECT task_id,stage,iteration FROM submissions'),
                         [('A', 'work', 1)])

    def test_validation_preparation_allows_independent_commit(self):
        self.prepared_call(lambda: self.commands.validate_submission(
            'A', 'actor-a', deepcopy(self.payload)))
        self.assertEqual(rows(self.path, 'SELECT count(*) FROM submissions'), [(0,)])
        self.assertEqual(rows(self.path, 'SELECT version FROM tasks WHERE id=?', ('A',)), [(0,)])

    def test_matching_preparation_allows_independent_commit(self):
        value = self.prepared_call(lambda: self.commands.matching_submission_digest(
            'A', 'actor-a', deepcopy(self.payload)))
        self.assertIsNone(value)
        self.assertEqual(rows(self.path, 'SELECT count(*) FROM submissions'), [(0,)])

    def restored_call(self, operation):
        self.active()
        gate = PreparationGate()
        original = TaskStageContracts.parse

        def restore(*args, **kwargs):
            # Only A has stage contracts; B is an untyped newborn.
            gate.pause()
            return original(*args, **kwargs)

        with patch.object(TaskStageContracts, 'parse', restore):
            outcome = overlap(gate, operation, self.write_b, self.observe_b)
        self.assert_a_succeeded(outcome)
        return outcome[1]

    def test_read_projection_restore_allows_independent_commit(self):
        value = self.restored_call(lambda: self.commands.workflow_context('A'))
        self.assertEqual(value['visits'], {'work': 1})
        self.assertEqual(rows(self.path, 'SELECT count(*) FROM submissions'), [(0,)])

    def test_submission_restore_allows_independent_commit(self):
        self.restored_call(self.submit)
        self.assertEqual(rows(self.path, 'SELECT task_id,stage,iteration FROM submissions'),
                         [('A', 'work', 1)])
