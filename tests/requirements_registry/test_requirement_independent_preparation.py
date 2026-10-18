"""Integration subject: registry preparation, global revision and independent commit."""
from copy import deepcopy
from unittest.mock import patch

from independent_write_support import RequirementCase, PreparationGate, fixture, overlap
from poise.modules.foundation.errors import VersionConflict
from poise.modules.requirements_registry.domain import RequirementsRegistry


class IndependentRequirementPreparation(RequirementCase):
    def test_registry_preparation_allows_independent_commit(self):
        gate = PreparationGate()
        original = RequirementsRegistry.apply

        def prepare(registry, operations, max_items):
            if operations[0]['requirement']['id'] == 'APP-A':
                gate.pause()
            return original(registry, operations, max_items)

        with patch.object(RequirementsRegistry, 'apply', prepare):
            outcome = overlap(
                gate, self.apply,
                lambda: self.apply(fixture('requirements_registry', 'apply_b.json')),
                lambda: self.assert_expected('expected_after_b.json'),
            )
        self.assertEqual(outcome[0], 'error')
        self.assertIsInstance(outcome[1], VersionConflict)
        self.assert_expected('expected_after_b.json')
        with self.assertRaises(VersionConflict):
            self.apply()
        current = deepcopy(self.request)
        current.update(request_id='apply-a-current', expected_revision=1)
        accepted = self.apply(current)
        self.assertEqual(accepted, {'revision': 2, 'replayed': False})
        self.assertEqual(self.apply(current), {'revision': 2, 'replayed': True})
