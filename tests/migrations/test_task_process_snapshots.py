from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import Poise, write_json
from tests.backups.helpers import backup_commands, backup_directory, run_cli


TASKS = ("0077", "0081", "0079", "0078", "0074")
FIXED_NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)


def _database(project: dict) -> Path:
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    state = Path(config["paths"]["state"])
    if not state.is_absolute():
        state = project["config_path"].parent / state
    return state / config["paths"]["database"]


def _configured_processes(project: dict) -> dict[str, dict]:
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    development = json.loads(project["process_path"].read_text(encoding="utf-8"))
    documentation = deepcopy(development)
    documentation["goal_type"] = "documentation"
    documentation["worktree_required"] = False
    documentation_path = project["root"] / "config/processes/documentation.json"
    write_json(documentation_path, documentation)
    config["processes"]["documentation"] = "config/processes/documentation.json"
    write_json(project["config_path"], config)
    return {"development": development, "documentation": documentation}


def _seed_legacy_tasks(project: dict) -> tuple[Path, dict[str, dict]]:
    from poise.infrastructure.sqlite.database import Database

    processes = _configured_processes(project)
    Poise(project["config_path"], "schema-initializer")
    database = _database(project)
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    state = database.parent
    lock = state / config["paths"]["lock"]
    db = Database(database, lock, 2.0, 0.01)
    expected = {}
    with db.transaction() as connection:
        for index, task_id in enumerate(TASKS):
            goal_type = "documentation" if task_id == "0074" else "development"
            process = deepcopy(processes[goal_type])
            process.pop("worktree_required")
            metadata = {
                "contract": {"id": task_id, "goal_type": goal_type},
                "goal": f"legacy-{task_id}",
                "process": process,
                "marker": {"preserve": task_id},
            }
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    "available",
                    0,
                    1,
                    None,
                    7 + index,
                    None,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            expected[task_id] = metadata
    return database, expected


def _request(backup_name: str, **changes: object) -> dict:
    value = {
        "schema": "task-process-migration-1",
        "request_id": "migrate-chain-processes-1",
        "backup_name": backup_name,
        "task_ids": list(TASKS),
        "authorization": "User authorized migration of 0077, 0081, 0079, 0078 and 0074.",
    }
    value.update(changes)
    return value


def _snapshot(database: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(database) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: list(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            for table in tables
        }


def _invoke_cli(project: dict, request: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "poise", "task-process-migrate", "--config", str(project["config_path"])],
        cwd=Path(__file__).resolve().parents[2],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )


def test_domain_plans_only_missing_field_for_exact_batch():
    from poise.modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration

    request = MigrationRequest.parse(_request("tasks-backup.sqlite"))
    rows = []
    processes = {}
    for task_id in TASKS:
        goal_type = "documentation" if task_id == "0074" else "development"
        process = {"goal_type": goal_type, "route": {"entry": "start"}}
        rows.append({"id": task_id, "status": "available", "claimed_by": None,
                     "metadata": {"contract": {"goal_type": goal_type}, "process": process}})
        processes[goal_type] = {**process, "worktree_required": goal_type != "documentation"}

    plan = ProcessSnapshotMigration.plan(request, rows, processes)

    assert tuple(item.task_id for item in plan.updates) == TASKS
    assert [item.process["worktree_required"] for item in plan.updates] == [True, True, True, True, False]
    assert all(set(item.process) == {"goal_type", "route", "worktree_required"} for item in plan.updates)


def test_migration_preserves_task_state_and_replays_receipt(project):
    from poise.modules.hook_transport.domain import BoundSourceRoute

    database, expected_metadata = _seed_legacy_tasks(project)
    backup = backup_commands(project).create()
    backup_path = backup_directory(project) / backup["name"]
    backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    before = _snapshot(database)

    first = _invoke_cli(project, _request(backup["name"]))

    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["status"] == "migrated"
    assert result["replayed"] is False
    assert result["request_id"] == "migrate-chain-processes-1"
    assert result["backup_name"] == backup["name"]
    assert [item["task_id"] for item in result["tasks"]] == list(TASKS)
    assert [item["worktree_required"] for item in result["tasks"]] == [True, True, True, True, False]
    assert len(result["receipt"]) == 64
    assert hashlib.sha256(backup_path.read_bytes()).hexdigest() == backup_digest

    after = _snapshot(database)
    assert {key: value for key, value in after.items() if key not in {"tasks", "journal"}} == {
        key: value for key, value in before.items() if key not in {"tasks", "journal"}
    }
    assert len(after["journal"]) == len(before["journal"]) + 1
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
            "FROM tasks ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == sorted(TASKS)
    for row in rows:
        task_id, status, stage_index, iteration, claimed_by, version, submission, raw = row
        assert (status, stage_index, iteration, claimed_by, version, submission) == (
            "available", 0, 1, None, 7 + TASKS.index(task_id), None
        )
        metadata = json.loads(raw)
        expected = deepcopy(expected_metadata[task_id])
        expected["process"]["worktree_required"] = task_id != "0074"
        assert metadata == expected
        assert BoundSourceRoute.decide("bootstrap", {"id": task_id}, None, {
            "id": task_id, "status": status, "worktree": None, "process": metadata["process"]
        }).source == "installation"

    replay = _invoke_cli(project, _request(backup["name"]))
    assert replay.returncode == 0, replay.stderr
    replayed = json.loads(replay.stdout)
    assert replayed == {**result, "replayed": True}
    assert _snapshot(database) == after


def test_rolls_back_complete_batch_and_receipt_on_fault(project):
    from poise.application.task_process_migration import TaskProcessMigrationCommands
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

    database, _ = _seed_legacy_tasks(project)
    backup = backup_commands(project).create()
    before = _snapshot(database)

    def fail_after_first(task_id: str) -> None:
        if task_id == TASKS[0]:
            raise RuntimeError("injected migration failure")

    port = SqliteTaskProcessMigration(project["config_path"], clock=lambda: FIXED_NOW,
                                      after_update=fail_after_first)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        TaskProcessMigrationCommands(port).migrate(_request(backup["name"]))

    assert _snapshot(database) == before


def test_rejects_invalid_targets_and_conflicting_process_values():
    from poise.modules.foundation.errors import DomainError
    from poise.modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration

    request = MigrationRequest.parse(_request("tasks-backup.sqlite"))
    configured = {"development": {"goal_type": "development", "worktree_required": True}}
    good = [{"id": task_id, "status": "available", "claimed_by": None,
             "metadata": {"contract": {"goal_type": "development"},
                          "process": {"goal_type": "development"}}} for task_id in TASKS]
    variants = []
    variants.append(good[:-1])
    terminal = deepcopy(good); terminal[0]["status"] = "completed"; variants.append(terminal)
    claimed = deepcopy(good); claimed[0]["claimed_by"] = "foreign"; variants.append(claimed)
    conflicting = deepcopy(good); conflicting[0]["metadata"]["process"]["worktree_required"] = False; variants.append(conflicting)
    mismatch = deepcopy(good); mismatch[0]["metadata"]["process"]["goal_type"] = "documentation"; variants.append(mismatch)
    for rows in variants:
        with pytest.raises(DomainError):
            ProcessSnapshotMigration.plan(request, rows, configured)


def test_stale_backup_or_live_row_drift_rejects_every_update(project):
    from poise.modules.foundation.errors import PoiseError

    database, expected = _seed_legacy_tasks(project)
    backup = backup_commands(project).create()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", (TASKS[0],))
    before = _snapshot(database)

    result = _invoke_cli(project, _request(backup["name"]))

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "rejected"
    assert "backup" in payload["reason"].lower()
    assert _snapshot(database) == before
    assert all("worktree_required" not in item["process"] for item in expected.values())


def test_cli_requires_exact_authorized_batch(project):
    from poise.modules.tasks.process_migration import MigrationRequest

    invalid = [
        {},
        _request("tasks-backup.sqlite", schema="unknown"),
        _request("../tasks-backup.sqlite"),
        _request("tasks-backup.sqlite", task_ids=["0077", "0077"]),
        _request("tasks-backup.sqlite", authorization=""),
        {**_request("tasks-backup.sqlite"), "extra": True},
    ]
    for value in invalid:
        with pytest.raises(Exception):
            MigrationRequest.parse(value)

    result = run_cli("task-process-migrate", "--config", str(project["config_path"]))
    assert result.returncode == 2
    assert "traceback" not in result.stderr.lower()
