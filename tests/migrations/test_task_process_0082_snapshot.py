from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import pytest

import conftest
from conftest import Poise
from conftest import project as project_fixture
from poise.common import digest
from poise.modules.foundation.errors import DomainError
from tests.backups.helpers import backup_commands, backup_directory


TASKS_V1 = ("0077", "0081", "0079", "0078", "0074", "0063")


class Task0082ProcessMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.monkeypatch = pytest.MonkeyPatch()
        self.monkeypatch.setattr(conftest, "git", self._quiet_git)
        self.project = project_fixture.__wrapped__(
            Path(self.temporary.name), self.monkeypatch
        )

    def tearDown(self):
        self.monkeypatch.undo()
        self.temporary.cleanup()

    @staticmethod
    def _quiet_git(root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def _database(self) -> Path:
        config = json.loads(
            self.project["config_path"].read_text(encoding="utf-8")
        )
        state = Path(config["paths"]["state"])
        if not state.is_absolute():
            state = self.project["config_path"].parent / state
        return state / config["paths"]["database"]

    def _configured_process(self) -> dict:
        config = json.loads(
            self.project["config_path"].read_text(encoding="utf-8")
        )
        path = self.project["config_path"].parent / config["processes"]["development"]
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _request(backup_name: str, **changes: object) -> dict:
        value = {
            "schema": "task-process-migration-2",
            "request_id": "migrate-task-0082-process-1",
            "backup_name": backup_name,
            "task_ids": ["0082"],
            "authorization": "User authorized process snapshot recovery for Task 0082.",
        }
        value.update(changes)
        return value

    @staticmethod
    def _snapshot(database: Path) -> dict[str, list[tuple]]:
        with sqlite3.connect(database) as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            return {
                table: list(
                    connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                )
                for table in tables
            }

    def _seed_legacy_0082(self) -> tuple[Path, dict]:
        from poise.infrastructure.sqlite.database import Database

        Poise(self.project["config_path"], "schema-initializer")
        database = self._database()
        config = json.loads(
            self.project["config_path"].read_text(encoding="utf-8")
        )
        state = Path(config["paths"]["state"])
        if not state.is_absolute():
            state = self.project["config_path"].parent / state
        legacy_process = deepcopy(self._configured_process())
        legacy_process.pop("worktree_required")
        metadata = {
            "config_hash": "preserve-config-identity",
            "contract": {"goal_type": "development", "id": "0082"},
            "goal": "preserve legacy Task 0082",
            "marker": {"preserve": "0082"},
            "process": legacy_process,
        }
        store = Database(
            database,
            state / config["paths"]["lock"],
            2.0,
            0.01,
        )
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,"
                "current_submission_id,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "0082",
                    "active",
                    5,
                    8,
                    None,
                    37,
                    None,
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                (
                    "0082",
                    37,
                    "2026-09-13T08:00:00+00:00",
                    '{"event":"empty_rework_recovered"}',
                ),
            )
            connection.execute(
                "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
                (
                    "0082",
                    '{"branch":"tasks/0082","marker":"preserve","worktree":"/preserve"}',
                    11,
                ),
            )
            connection.execute(
                "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
                (
                    "2026-09-13T08:00:00+00:00",
                    "preserved-session",
                    "0082",
                    "preserved-event",
                    '{"marker":"preserve"}',
                ),
            )
        return database, metadata

    def _invoke_cli(self, request: dict) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        root = Path(__file__).resolve().parents[2]
        environment["PYTHONPATH"] = str(root / "src") + os.pathsep + environment.get(
            "PYTHONPATH", ""
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "poise",
                "task-process-migrate",
                "--config",
                str(self.project["config_path"]),
            ],
            cwd=root,
            env=environment,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_schema_2_accepts_only_task_0082(self):
        from poise.modules.tasks.process_migration import MigrationRequest

        parsed = MigrationRequest.parse(self._request("tasks-backup.sqlite"))
        self.assertEqual(parsed.task_ids, ("0082",))
        parsed_v1 = MigrationRequest.parse(
            {
                "schema": "task-process-migration-1",
                "request_id": "preserve-schema-1",
                "backup_name": "tasks-backup.sqlite",
                "task_ids": list(TASKS_V1),
                "authorization": "Preserve the original authorization contract.",
            }
        )
        self.assertEqual(parsed_v1.task_ids, TASKS_V1)
        for task_ids in ([], ["0082", "0077"], ["0077"]):
            with self.assertRaisesRegex(DomainError, "exactly 0082"):
                MigrationRequest.parse(
                    self._request("tasks-backup.sqlite", task_ids=task_ids)
                )

    def test_domain_plans_only_missing_worktree_field(self):
        from poise.modules.tasks.process_migration import (
            MigrationRequest,
            ProcessSnapshotMigration,
        )

        configured = self._configured_process()
        legacy = deepcopy(configured)
        legacy.pop("worktree_required")
        request = MigrationRequest.parse(self._request("tasks-backup.sqlite"))

        plan = ProcessSnapshotMigration.plan(
            request,
            [
                {
                    "id": "0082",
                    "status": "active",
                    "claimed_by": None,
                    "metadata": {
                        "contract": {"goal_type": "development"},
                        "process": legacy,
                    },
                }
            ],
            {"development": configured},
        )

        self.assertEqual(tuple(item.task_id for item in plan.updates), ("0082",))
        self.assertEqual(plan.updates[0].process, configured)

    def test_public_migration_preserves_task_0082_and_replays(self):
        from poise.modules.tasks.process_migration import MigrationRequest

        database, metadata = self._seed_legacy_0082()
        backup = backup_commands(self.project).create()
        backup_path = backup_directory(self.project) / backup["name"]
        backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        before = self._snapshot(database)
        request = self._request(backup["name"])

        first = self._invoke_cli(request)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        migrated = json.loads(first.stdout)
        self.assertEqual(migrated["status"], "migrated")
        self.assertFalse(migrated["replayed"])
        self.assertEqual([item["task_id"] for item in migrated["tasks"]], ["0082"])
        self.assertTrue(migrated["tasks"][0]["worktree_required"])
        self.assertEqual(hashlib.sha256(backup_path.read_bytes()).hexdigest(), backup_digest)

        after = self._snapshot(database)
        self.assertEqual(
            {key: value for key, value in after.items() if key not in {"tasks", "journal"}},
            {key: value for key, value in before.items() if key not in {"tasks", "journal"}},
        )
        self.assertEqual(len(after["journal"]), len(before["journal"]) + 1)
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
                "FROM tasks WHERE id='0082'"
            ).fetchone()
        self.assertEqual(row[:6], ("active", 5, 8, None, 37, None))
        migrated_metadata = json.loads(row[6])
        expected_metadata = deepcopy(metadata)
        expected_metadata["process"]["worktree_required"] = True
        self.assertEqual(migrated_metadata, expected_metadata)

        parsed = MigrationRequest.parse(request)
        expected_task = {
            "task_id": "0082",
            "worktree_required": True,
            "old_process_digest": digest(metadata["process"]),
            "new_process_digest": digest(expected_metadata["process"]),
        }
        self.assertEqual(migrated["tasks"], [expected_task])
        self.assertEqual(
            migrated["receipt"],
            digest(
                {
                    "request_digest": parsed.digest,
                    "backup_name": backup["name"],
                    "tasks": [expected_task],
                }
            ),
        )
        migrated_snapshot = self._snapshot(database)

        replay = self._invoke_cli(request)

        self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        replayed = json.loads(replay.stdout)
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["receipt"], migrated["receipt"])
        self.assertEqual(self._snapshot(database), migrated_snapshot)


if __name__ == "__main__":
    unittest.main()
