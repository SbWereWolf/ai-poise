from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
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
from conftest import WorkPoise
from conftest import project as project_fixture
from conftest import write_json
from batch.helpers import request
from poise.application.work import WorkTools
from poise.application.backups import BackupCommands
from poise.common import digest
from poise.infrastructure.backups import LocalTaskDatabaseBackups
from poise.modules.foundation.errors import DomainError
from runner.test_runner_paths import edit, result as stage_result, setup_project
from tests.backups.helpers import FIXED_NOW, backup_commands, backup_directory


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
            "schema": "task-process-migration-3",
            "request_id": "migrate-task-0082-stage-contracts-2",
            "backup_name": backup_name,
            "task_ids": ["0082"],
            "authorization": "User authorized process snapshot recovery for Task 0082.",
        }
        value.update(changes)
        return value

    @staticmethod
    def _legacy_request(backup_name: str) -> dict:
        return {
            "schema": "task-process-migration-2",
            "request_id": "migrate-task-0082-process-1",
            "backup_name": backup_name,
            "task_ids": ["0082"],
            "authorization": "User authorized process snapshot recovery for Task 0082.",
        }

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

    def _seed_legacy_0082(self) -> tuple[Path, dict, list[dict]]:
        self.project["task"]["id"] = "0082"
        setup_project(self.project, "development")
        process_path = self.project["root"] / "config/processes/development.json"
        process = json.loads(process_path.read_text(encoding="utf-8"))
        process["route"]["entry"] = "test_remediation"
        for stage in process["stages"]:
            if stage["id"] == "draft":
                stage["id"] = "test_remediation"
            stage["transitions"] = {
                outcome: "test_remediation" if target == "draft" else target
                for outcome, target in stage["transitions"].items()
            }
            stage["rework_targets"] = [
                "test_remediation" if target == "draft" else target
                for target in stage["rework_targets"]
            ]
        write_json(process_path, process)

        task = self.project["task"]
        task["checks"]["test_remediation"] = task["checks"].pop("draft")
        task["evidence_plan"]["test_remediation"] = task["evidence_plan"].pop(
            "draft"
        )
        requirements = (
            process["content_contract"]["requirements"]
            + task["content_contract"]["requirements"]
        )
        task["stage_contracts"] = [
            {
                "stage_id": stage["id"],
                "allowed_paths": list(stage["allowed_paths"]),
                "entry_requirements": [
                    item["id"] for item in requirements
                    if stage["id"] in item["stages"] and item["phase"] == "pre"
                ],
                "exit_requirements": [
                    item["id"] for item in requirements
                    if stage["id"] in item["stages"] and item["phase"] == "post"
                ],
            }
            for stage in process["stages"]
        ]
        for method in task["methods"]:
            plan = method["verification_plan"]
            plan["red_stages"] = [
                "test_remediation" if stage == "draft" else stage
                for stage in plan["red_stages"]
            ]
            plan["green_stages"] = [
                "test_remediation" if stage == "draft" else stage
                for stage in plan["green_stages"]
            ]
        write_json(self.project["task_path"], task)

        runtime = Poise(self.project["config_path"], "S1")
        context = runtime.bootstrap(task_file=self.project["task_path"])
        self.assertEqual(context["stage"], "test_remediation")
        edit(context, "development", "verified iteration 1\n")
        stage_result(context, {})
        verified = runtime.verify()
        for iteration in range(2, 9):
            context = runtime.bootstrap(
                decision="rework",
                feedback=f"Preserve legacy feedback for iteration {iteration}.",
                rework_stage="test_remediation",
            )
            stage_result(context, {})
            if iteration == 8:
                artifact = Path(context["task_root"]) / "preserved-0082.txt"
                artifact.write_text("preserve artifact\n", encoding="utf-8")
                context["result_template"]["artifact_paths"] = [str(artifact)]
            verified = runtime.verify()
        self.assertEqual(verified["stage"], "test_remediation")
        self.assertEqual(verified["iteration"], 8)
        WorkTools(runtime).invoke(
            request(
                "handoff",
                {
                    "request_id": "release-legacy-0082",
                    "reason": "Preserve released reviewer context.",
                    "result": None,
                    "commit_message": None,
                    "artifact_paths": [],
                },
            )
        )

        database = self._database()
        with runtime.store.transaction() as connection:
            raw = connection.execute(
                "SELECT metadata FROM tasks WHERE id='0082'"
            ).fetchone()[0]
            metadata = json.loads(raw)
            stage_contracts = metadata["contract"].pop("stage_contracts")
            legacy_process = metadata["process"]
            stage = next(
                item for item in legacy_process["stages"]
                if item["id"] == "test_remediation"
            )
            stage["instruction"] = "Preserve the legacy Task 0082 instruction."
            legacy_process.pop("worktree_required")
            connection.execute(
                "UPDATE tasks SET metadata=? WHERE id='0082'",
                (
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        return database, metadata, stage_contracts

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

    def test_schema_2_and_3_accept_only_task_0082(self):
        from poise.modules.tasks.process_migration import MigrationRequest

        parsed = MigrationRequest.parse(self._request("tasks-backup.sqlite"))
        self.assertEqual(parsed.task_ids, ("0082",))
        parsed_legacy = MigrationRequest.parse(self._legacy_request("tasks-backup.sqlite"))
        self.assertEqual(parsed_legacy.task_ids, ("0082",))
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

    def test_domain_plans_only_missing_worktree_and_stage_contract_fields(self):
        from poise.modules.tasks.process_migration import (
            MigrationRequest,
            ProcessSnapshotMigration,
        )

        configured = self._configured_process()
        legacy = deepcopy(configured)
        legacy.pop("worktree_required")
        legacy["stages"][0]["instruction"] = "Preserve a legacy instruction."
        contract = deepcopy(self.project["task"])
        contract["id"] = "0082"
        contract.pop("stage_contracts")
        request = MigrationRequest.parse(self._request("tasks-backup.sqlite"))

        plan = ProcessSnapshotMigration.plan(
            request,
            [
                {
                    "id": "0082",
                    "status": "active",
                    "claimed_by": None,
                    "metadata": {
                        "contract": contract,
                        "process": legacy,
                    },
                }
            ],
            {"development": configured},
        )

        self.assertEqual(tuple(item.task_id for item in plan.updates), ("0082",))
        expected = deepcopy(legacy)
        expected["worktree_required"] = True
        self.assertEqual(plan.updates[0].process, expected)
        self.assertEqual(
            [item["stage_id"] for item in plan.updates[0].contract["stage_contracts"]],
            [item["id"] for item in legacy["stages"]],
        )

    def test_schema_3_finishes_partial_process_recovery_without_replacing_it(self):
        from poise.modules.tasks.process_migration import (
            MigrationRequest,
            ProcessSnapshotMigration,
        )

        configured = self._configured_process()
        legacy = deepcopy(configured)
        legacy["stages"][0]["instruction"] = "Preserve partial recovery state."
        contract = deepcopy(self.project["task"])
        contract["id"] = "0082"
        contract.pop("stage_contracts")
        request = MigrationRequest.parse(self._request("tasks-backup.sqlite"))

        plan = ProcessSnapshotMigration.plan(
            request,
            [{
                "id": "0082",
                "status": "verified",
                "claimed_by": None,
                "metadata": {"contract": contract, "process": legacy},
            }],
            {"development": configured},
        )

        update = plan.updates[0]
        self.assertFalse(update.worktree_required_added)
        self.assertEqual(update.process, legacy)
        self.assertEqual(update.old_process_digest, update.new_process_digest)
        self.assertIn("stage_contracts", update.contract)

    def test_schema_3_rejects_nonexact_partial_recovery(self):
        from poise.modules.tasks.process_migration import (
            MigrationRequest,
            ProcessSnapshotMigration,
        )

        configured = self._configured_process()
        contract = deepcopy(self.project["task"])
        contract["id"] = "0082"
        request = MigrationRequest.parse(self._request("tasks-backup.sqlite"))

        for process, existing_contract, reason in (
            (deepcopy(configured), contract, "already has stage_contracts"),
            (
                {**deepcopy(configured), "worktree_required": False},
                {key: value for key, value in contract.items() if key != "stage_contracts"},
                "conflicting worktree_required",
            ),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(DomainError, reason):
                ProcessSnapshotMigration.plan(
                    request,
                    [{
                        "id": "0082",
                        "status": "verified",
                        "claimed_by": None,
                        "metadata": {"contract": existing_contract, "process": process},
                    }],
                    {"development": configured},
                )

    def test_public_migration_preserves_task_0082_and_replays(self):
        from poise.modules.tasks.process_migration import MigrationRequest

        database, metadata, stage_contracts = self._seed_legacy_0082()
        backup = backup_commands(self.project).create()
        backup_path = backup_directory(self.project) / backup["name"]
        backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        before = self._snapshot(database)
        before_task = next(row for row in before["tasks"] if row[0] == "0082")
        for table in (
            "artifacts",
            "evidence",
            "handoffs",
            "submissions",
            "task_artifacts",
            "task_events",
            "task_execution",
            "task_proofs",
            "task_results",
            "task_workflows",
            "workflow_layers",
        ):
            self.assertTrue(before[table], table)
        self.assertTrue(
            any("Preserve legacy feedback for iteration 8" in row[-1]
                for row in before["task_events"])
        )
        migration_request = self._request(backup["name"])

        first = self._invoke_cli(migration_request)

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
        self.assertEqual(row[:6], before_task[1:7])
        migrated_metadata = json.loads(row[6])
        expected_metadata = deepcopy(metadata)
        expected_metadata["process"]["worktree_required"] = True
        expected_metadata["contract"]["stage_contracts"] = stage_contracts
        self.assertEqual(migrated_metadata, expected_metadata)
        preserved_stage = next(
            item for item in migrated_metadata["process"]["stages"]
            if item["id"] == "test_remediation"
        )
        self.assertEqual(
            preserved_stage["instruction"],
            "Preserve the legacy Task 0082 instruction.",
        )

        parsed = MigrationRequest.parse(migration_request)
        expected_task = {
            "task_id": "0082",
            "worktree_required": True,
            "worktree_required_added": True,
            "stage_contracts_initialized": True,
            "old_process_digest": digest(metadata["process"]),
            "new_process_digest": digest(expected_metadata["process"]),
            "old_contract_digest": digest(metadata["contract"]),
            "new_contract_digest": digest(expected_metadata["contract"]),
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

        replay = self._invoke_cli(migration_request)

        self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        replayed = json.loads(replay.stdout)
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["receipt"], migrated["receipt"])
        self.assertEqual(self._snapshot(database), migrated_snapshot)

        reviewer = WorkTools(
            WorkPoise(self.project["config_path"], "REVIEWER")
        ).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "0082"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )

        self.assertEqual(reviewer["status"], "verified")
        self.assertEqual(reviewer["stage"], "test_remediation")
        self.assertEqual(reviewer["iteration"], 8)
        self.assertEqual(reviewer["task"], "0082")

    def test_public_schema_3_completes_real_schema_2_partial_state(self):
        database, metadata, stage_contracts = self._seed_legacy_0082()
        first_backup = backup_commands(self.project).create()
        legacy_request = self._legacy_request(first_backup["name"])

        first = self._invoke_cli(legacy_request)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        legacy_result = json.loads(first.stdout)
        self.assertEqual(
            set(legacy_result["tasks"][0]),
            {"task_id", "worktree_required", "old_process_digest", "new_process_digest"},
        )
        after_schema_2 = self._snapshot(database)
        before_task = next(row for row in after_schema_2["tasks"] if row[0] == "0082")
        partial_metadata = json.loads(before_task[7])
        self.assertTrue(partial_metadata["process"]["worktree_required"])
        self.assertNotIn("stage_contracts", partial_metadata["contract"])

        legacy_replay = self._invoke_cli(legacy_request)
        self.assertEqual(legacy_replay.returncode, 0, legacy_replay.stdout + legacy_replay.stderr)
        self.assertEqual(
            json.loads(legacy_replay.stdout),
            {**legacy_result, "replayed": True},
        )

        corrective_backup = BackupCommands(
            LocalTaskDatabaseBackups(self.project["config_path"]),
            clock=lambda: FIXED_NOW + timedelta(microseconds=1),
        ).create()
        corrective_request = self._request(corrective_backup["name"])
        corrective = self._invoke_cli(corrective_request)

        self.assertEqual(corrective.returncode, 0, corrective.stdout + corrective.stderr)
        corrective_result = json.loads(corrective.stdout)
        self.assertFalse(corrective_result["tasks"][0]["worktree_required_added"])
        self.assertEqual(
            corrective_result["tasks"][0]["old_process_digest"],
            corrective_result["tasks"][0]["new_process_digest"],
        )
        after_schema_3 = self._snapshot(database)
        after_task = next(row for row in after_schema_3["tasks"] if row[0] == "0082")
        self.assertEqual(after_task[:7], before_task[:7])
        completed_metadata = json.loads(after_task[7])
        self.assertEqual(completed_metadata["process"], partial_metadata["process"])
        self.assertEqual(completed_metadata["contract"]["stage_contracts"], stage_contracts)
        expected_contract = deepcopy(metadata["contract"])
        expected_contract["stage_contracts"] = stage_contracts
        self.assertEqual(completed_metadata["contract"], expected_contract)

        corrective_replay = self._invoke_cli(corrective_request)
        self.assertEqual(
            json.loads(corrective_replay.stdout),
            {**corrective_result, "replayed": True},
        )
        legacy_replay_after = self._invoke_cli(legacy_request)
        self.assertEqual(legacy_replay_after.returncode, 0, legacy_replay_after.stdout)
        self.assertEqual(
            json.loads(legacy_replay_after.stdout),
            {**legacy_result, "replayed": True},
        )

        reviewer = WorkTools(
            WorkPoise(self.project["config_path"], "REVIEWER-PARTIAL")
        ).invoke(
            request(
                "bootstrap",
                {
                    "task": {"id": "0082"},
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        self.assertEqual(
            (reviewer["status"], reviewer["stage"], reviewer["iteration"]),
            ("verified", "test_remediation", 8),
        )


if __name__ == "__main__":
    unittest.main()
