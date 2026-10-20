"""Creation builders release SQL before slow pure preparation."""
from creation_write_support import CreationCase
from independent_write_support import rows


class IndependentCreationPreparation(CreationCase):
    def test_direct_creation_allows_independent_commit(self):
        result, counts = self.creation_overlap(self.create)
        self.assertEqual(result.receipt(), {
            'request_id': 'create-a', 'task_id': '0001', 'replayed': False})
        self.assertEqual((counts['git'], counts['prepare']), (1, 1))
        self.assert_children('direct', sprint_id=None)

    def test_reviewed_tasks_creation_allows_independent_commit(self):
        self.seed_parent()
        result, counts = self.creation_overlap(self.publication)
        self.assert_publication(result, 'tasks')
        self.assertEqual((counts['git'], counts['prepare']), (2, 2))

    def test_reviewed_sprint_creation_allows_independent_commit(self):
        self.seed_parent('sprint')
        result, counts = self.creation_overlap(lambda: self.publication('sprint'))
        self.assert_publication(result, 'sprint')
        self.assertEqual((counts['git'], counts['prepare']), (2, 2))

    def test_sprint_newborn_promotion_allows_independent_commit(self):
        self.seed_sprint()
        result, counts = self.creation_overlap(self.sprint_publication)
        self.assert_sprint(result)
        self.assertEqual((counts['git'], counts['prepare']), (2, 2))

    def test_automatic_id_collision_recompiles_without_repeating_external_preparation(self):
        def write():
            outcome = self.create(self.intent('create-b', goal='Second child result.'), 'actor-c')
            self.assertEqual(outcome.receipt(), {
                'request_id': 'create-b', 'task_id': '0001', 'replayed': False})

        def observe():
            self.assert_children('competitor', sprint_id=None)

        result, counts = self.creation_overlap(self.create, write, observe)
        self.assertEqual(result.receipt(), {
            'request_id': 'create-a', 'task_id': '0002', 'replayed': False})
        self.assertEqual((counts['git'], counts['prepare']), (1, 1))
        self.assert_children('collision', sprint_id=None)

    def test_completed_creation_request_replays_after_preparation(self):
        def write():
            result = self.create(actor='actor-c')
            self.assertEqual(result.receipt(), {
                'request_id': 'create-a', 'task_id': '0001', 'replayed': False})

        def observe():
            self.assert_children('direct', sprint_id=None)

        result, counts = self.creation_overlap(self.create, write, observe)
        self.assertEqual(result.receipt(), {
            'request_id': 'create-a', 'task_id': '0001', 'replayed': True})
        self.assertEqual((counts['git'], counts['prepare']), (1, 1))
        observe()
        self.assertEqual(rows(self.path, 'SELECT id,task_id FROM sessions ORDER BY id'),
                         [('actor-a', None), ('actor-b', None), ('actor-c', None)])
