"""Cadence is based on actual snapshot time, not alphabetical labels."""
import json
from pathlib import Path
import tempfile
import unittest
import snapshot_flow

class SnapshotSelectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
    def record(self,name,timestamp):
        p=self.root/(name+'-receipt.json');p.write_text(json.dumps({'prepared_at_utc':timestamp,'name':name}));return p
    def test_no_prior_receipt(self):self.assertIsNone(snapshot_flow.latest_receipt(self.root))
    def test_timestamps_override_filename_order(self):
        self.record('zzz','2026-09-27T09:00:00+00:00');self.record('aaa','2026-09-27T09:11:00+00:00')
        self.assertEqual(snapshot_flow.latest_receipt(self.root)['name'],'aaa')
    def test_offsets_are_compared_as_instants(self):
        self.record('aaa','2026-09-27T12:00:00+03:00');self.record('zzz','2026-09-27T08:59:00+00:00')
        self.assertEqual(snapshot_flow.latest_receipt(self.root)['name'],'aaa')
    def test_naive_time_rejected(self):
        self.record('bad','2026-09-27T09:00:00')
        with self.assertRaises(ValueError):snapshot_flow.latest_receipt(self.root)
if __name__=='__main__':unittest.main()
