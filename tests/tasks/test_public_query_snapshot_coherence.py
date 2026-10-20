"""Related records and relocation context belong to one committed snapshot."""
from copy import deepcopy
from threading import current_thread
from unittest.mock import patch

from independent_write_support import PreparationGate
from public_query_support import PublicQueryCase
from poise.infrastructure import material_locations


class PublicQuerySnapshotCoherence(PublicQueryCase):
    def test_evidence_and_locations_are_one_snapshot(self):
        self.arrange_public('evidence')
        gate = PreparationGate()
        observed_maps = []
        original = material_locations.relocate_receipt

        def relocate(value, mapping):
            # Evidence DTO has semantic arguments but no mechanical path today.
            # Observe the real producer/consumer boundary; never invent a path
            # field in stored evidence or replace relocation with a stub.
            if (current_thread() is gate.owner and isinstance(value, dict)
                    and set(value) == {'plan', 'assessment', 'arguments', 'decisions', 'observations'}):
                observed_maps.append(deepcopy(mapping))
            return original(value, mapping)

        with patch.object(material_locations, 'relocate_receipt', relocate):
            with self.pause_public('evidence', gate):
                outcome = self.public_overlap('evidence', gate, self.operation('evidence'),
                                              self.write_related_evidence,
                                              self.observe_related_evidence)
        if outcome[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: evidence A failed after release') from outcome[1]
        self.assertEqual(outcome[1], self.expected['evidence'])
        self.assertEqual(observed_maps, [self.inputs['locations_old']])
        self.assertEqual(self.operation('evidence')(), self.expected['evidence_new'])
        self.assertEqual(self.store.queries.resolve_path('A', '/origin/report.txt'),
                         '/moved-new/report.txt')
        self.assertEqual(gate.calls, 1)

    def test_registry_context_and_rows_are_one_snapshot(self):
        self.arrange_public('registry')
        gate = PreparationGate()
        with self.pause_public('registry', gate):
            outcome = self.public_overlap('registry', gate, self.operation('registry'),
                                          self.write_related_registry,
                                          self.observe_related_registry)
        if outcome[0] != 'ok':
            raise RuntimeError('ARRANGEMENT: registry A failed after release') from outcome[1]
        self.assertEqual(outcome[1], self.expected['registry'])
        self.assertEqual(self.operation('registry')(), self.expected['registry_new'])
        self.assertEqual(gate.calls, 1)
