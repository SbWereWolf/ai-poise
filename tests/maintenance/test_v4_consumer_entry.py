"""The persistent v4 recipe publishes qualified consumer callbacks."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
V4 = ROOT / "config/maintenance/start-app/v4"

class ConsumerEntryContract(unittest.TestCase):
    def test_entry_preserves_consumer_result_contracts(self):
        entry = json.loads((V4 / "entry.example.json").read_text())
        self.assertEqual(entry["interpreters"], {
            "resource": {"module": "poise_environment.result_interpreters", "attribute": "maintenance_result"},
            "project-list": {"module": "poise_environment.result_interpreters", "attribute": "project_list_result"},
            "exit": {"module": "poise_environment.result_interpreters", "attribute": "exit_result"}})

    def test_publication_uses_the_v4_entry(self):
        profile = json.loads((V4 / "profile.example.json").read_text())
        entries = [item for item in profile["documents"] if item["destination"] == "entry.json"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "config/maintenance/start-app/v4/entry.example.json")

if __name__ == "__main__":
    unittest.main()
