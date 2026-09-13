from pathlib import Path
import unittest


class Task0082ProcessMigrationDocumentationTests(unittest.TestCase):
    def test_operator_contract_is_exact_and_complete(self):
        text = (
            Path(__file__).resolve().parents[2]
            / "docs/task-process-snapshot-migration.md"
        ).read_text(encoding="utf-8")
        required = (
            "`task-process-migration-2`",
            '`task_ids: ["0082"]`',
            "не расширяет разрешение `task-process-migration-1`",
            "публичную резервную копию",
            "`backup_name`",
            "`receipt`",
            "`replayed: true`",
            "lifecycle, verified result, stage, iteration, content, feedback, evidence, history, ownership, Git binding и config identity",
            "native bootstrap Task `0082`",
            "`stage_contracts`",
            "Task version и released handoff",
        )

        missing = [phrase for phrase in required if phrase not in text]

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
