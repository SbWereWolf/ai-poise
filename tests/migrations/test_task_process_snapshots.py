from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import Poise, write_json
from tests.backups.helpers import backup_commands, backup_directory, run_cli


TASKS = ("0077", "0081", "0079", "0078", "0074", "0063")
FIXED_NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)


class FakeClock:
    def observe(self):
        from poise.modules.accounting.clock import ClockObservation

        return ClockObservation(FIXED_NOW.isoformat(), 1, "task-process-migration-test")


def _database(project: dict) -> Path:
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    state = Path(config["paths"]["state"])
    if not state.is_absolute():
        state = project["config_path"].parent / state
    return state / config["paths"]["database"]


def _configured_processes(project: dict) -> dict[str, dict]:
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    development_path = project["config_path"].parent / config["processes"]["development"]
    development = json.loads(development_path.read_text(encoding="utf-8"))
    documentation = deepcopy(development)
    documentation["goal_type"] = "documentation"
    documentation["worktree_required"] = False
    documentation_path = project["root"] / "config/processes/documentation.json"
    write_json(documentation_path, documentation)
    analysis = deepcopy(development)
    analysis["goal_type"] = "analysis"
    analysis["worktree_required"] = True
    analysis_path = project["root"] / "config/processes/analysis.json"
    write_json(analysis_path, analysis)
    config["processes"]["documentation"] = "config/processes/documentation.json"
    config["processes"]["analysis"] = "config/processes/analysis.json"
    write_json(project["config_path"], config)
    return {"development": development, "documentation": documentation, "analysis": analysis}


def _configured_worktree_values(project: dict) -> dict[str, bool]:
    config = json.loads(project["config_path"].read_text(encoding="utf-8"))
    values = {}
    for goal_type, relative in config["processes"].items():
        process = json.loads(
            (project["config_path"].parent / relative).read_text(encoding="utf-8")
        )
        values[goal_type] = process["worktree_required"]
    return values


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
        for index, task_id in enumerate((*TASKS, "CONTROL")):
            goal_type = (
                "analysis" if task_id == "0063"
                else "documentation" if task_id == "0074"
                else "development"
            )
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
        submission = connection.execute(
            "INSERT INTO submissions(task_id,stage,iteration,digest,data) VALUES(?,?,?,?,?)",
            ("0077", "baseline", 1, "submission-digest", '{"report":"preserve"}'),
        ).lastrowid
        connection.execute(
            "UPDATE tasks SET current_submission_id=? WHERE id='0077'", (submission,)
        )
        connection.execute(
            "INSERT INTO task_workflows(task_id,data) VALUES(?,?)",
            ("0077", '{"history":["preserve"]}'),
        )
        connection.execute(
            "INSERT INTO workflow_layers(submission_id,task_id,data) VALUES(?,?,?)",
            (submission, "0077", '{"transition":"preserve"}'),
        )
        connection.execute(
            "INSERT INTO task_results(task_id,submission_id,data) VALUES(?,?,?)",
            ("0077", submission, '{"result":"preserve"}'),
        )
        connection.execute(
            "INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,?,?)",
            ("evidence-0077", "0077", "baseline", 1, '{"proof":"preserve"}'),
        )
        connection.execute(
            "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
            ("0077", 7, "2026-09-13T08:00:00+00:00", '{"event":"preserve"}'),
        )
        connection.execute(
            "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
            ("0077", '{"worktree":null,"marker":"preserve"}', 3),
        )
        submission_0063 = connection.execute(
            "INSERT INTO submissions(task_id,stage,iteration,digest,data) VALUES(?,?,?,?,?)",
            ("0063", "framing", 1, "submission-0063-digest", '{"report":"preserve-0063"}'),
        ).lastrowid
        connection.execute(
            "UPDATE tasks SET current_submission_id=? WHERE id='0063'", (submission_0063,)
        )
        connection.execute(
            "INSERT INTO task_workflows(task_id,data) VALUES(?,?)",
            ("0063", '{"history":["preserve-0063"]}'),
        )
        connection.execute(
            "INSERT INTO workflow_layers(submission_id,task_id,data) VALUES(?,?,?)",
            (submission_0063, "0063", '{"transition":"preserve-0063"}'),
        )
        connection.execute(
            "INSERT INTO task_results(task_id,submission_id,data) VALUES(?,?,?)",
            ("0063", submission_0063, '{"result":"preserve-0063"}'),
        )
        connection.execute(
            "INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,?,?)",
            ("evidence-0063", "0063", "framing", 1, '{"proof":"preserve-0063"}'),
        )
        connection.execute(
            "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
            ("0063", 12, "2026-09-13T08:01:00+00:00", '{"event":"preserve-0063"}'),
        )
        connection.execute(
            "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
            ("0063", '{"worktree":null,"marker":"preserve-0063"}', 4),
        )
        connection.execute(
            "INSERT INTO artifacts(id,owner,scope,path,digest) VALUES(?,?,?,?,?)",
            ("artifact-0063", "0063", "task", "deliverables/0063.md", "artifact-digest-0063"),
        )
        connection.execute(
            "INSERT INTO task_artifacts(task_id,artifact_id) VALUES(?,?)",
            ("0063", "artifact-0063"),
        )
        connection.execute(
            "INSERT INTO sprints(id,project,state,revision,data) VALUES(?,?,?,?,?)",
            ("MIGRATION-FIXTURE", "demo", "active", 1, '{"marker":"preserve"}'),
        )
        connection.executemany(
            "INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)",
            [("MIGRATION-FIXTURE", "0077"), ("MIGRATION-FIXTURE", "0081")],
        )
        connection.execute(
            "INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)",
            ("MIGRATION-FIXTURE", "0077", "0081", "result_availability"),
        )
    return database, expected


def _request(backup_name: str, **changes: object) -> dict:
    value = {
        "schema": "task-process-migration-1",
        "request_id": "migrate-chain-processes-1",
        "backup_name": backup_name,
        "task_ids": list(TASKS),
        "authorization": "User authorized migration of 0077, 0081, 0079, 0078, 0074 and 0063.",
    }
    value.update(changes)
    return value


def _digest(value: object) -> str:
    packed = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


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
    environment = os.environ.copy()
    root = Path(__file__).resolve().parents[2]
    source = str(root / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "poise", "task-process-migrate", "--config", str(project["config_path"])],
        cwd=root,
        env=environment,
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
        goal_type = (
            "analysis" if task_id == "0063"
            else "documentation" if task_id == "0077"
            else "development"
        )
        process = {"goal_type": goal_type, "route": {"entry": "start"}}
        rows.append({"id": task_id, "status": "available", "claimed_by": None,
                     "metadata": {"contract": {"goal_type": goal_type}, "process": process}})
        configured_value = {"documentation": False, "development": True, "analysis": False}[goal_type]
        processes[goal_type] = {**process, "worktree_required": configured_value}

    plan = ProcessSnapshotMigration.plan(request, rows, processes)

    assert tuple(item.task_id for item in plan.updates) == TASKS
    assert [item.process["worktree_required"] for item in plan.updates] == [False, True, True, True, True, False]
    assert all(set(item.process) == {"goal_type", "route", "worktree_required"} for item in plan.updates)


def test_migration_preserves_task_state_and_replays_receipt(project):
    from poise.modules.tasks.process_migration import MigrationRequest
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
    assert [item["worktree_required"] for item in result["tasks"]] == [True, True, True, True, False, True]
    configured_values = _configured_worktree_values(project)
    expected_tasks = []
    for task_id in TASKS:
        old_process = expected_metadata[task_id]["process"]
        goal_type = expected_metadata[task_id]["contract"]["goal_type"]
        worktree_required = configured_values[goal_type]
        new_process = {**old_process, "worktree_required": worktree_required}
        expected_tasks.append({
            "task_id": task_id,
            "worktree_required": worktree_required,
            "old_process_digest": _digest(old_process),
            "new_process_digest": _digest(new_process),
        })
    assert result["tasks"] == expected_tasks
    parsed_request = MigrationRequest.parse(_request(backup["name"]))
    expected_receipt = _digest({
        "request_digest": parsed_request.digest,
        "backup_name": backup["name"],
        "tasks": expected_tasks,
    })
    assert result["receipt"] == expected_receipt
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
    assert [row[0] for row in rows] == sorted((*TASKS, "CONTROL"))
    submission_by_task = {row[1]: row[0] for row in before["submissions"]}
    for row in rows:
        task_id, status, stage_index, iteration, claimed_by, version, submission, raw = row
        index = (*TASKS, "CONTROL").index(task_id)
        expected_submission = submission_by_task.get(task_id)
        assert (status, stage_index, iteration, claimed_by, version, submission) == (
            "available", 0, 1, None, 7 + index, expected_submission
        )
        metadata = json.loads(raw)
        expected = deepcopy(expected_metadata[task_id])
        if task_id in TASKS:
            expected["process"]["worktree_required"] = configured_values[
                expected["contract"]["goal_type"]
            ]
        assert metadata == expected
        if task_id in TASKS:
            route = BoundSourceRoute.decide("bootstrap", {"id": task_id}, None, {
                "id": task_id, "status": status, "worktree": None, "process": metadata["process"]
            })
            expected_source = "target_task" if metadata["process"]["worktree_required"] else "installation"
            assert route.source == expected_source

    with sqlite3.connect(database) as connection:
        journal = connection.execute(
            "SELECT at,session_id,task_id,event,data FROM journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert journal[1:4] == ("task-process-migrate", None, "task_process_snapshot_migrated")
    assert datetime.fromisoformat(journal[0]).utcoffset() == timedelta(0)
    audit = json.loads(journal[4])
    assert set(audit) == {"request_digest", "request_id", "result"}
    assert audit["request_id"] == result["request_id"]
    assert audit["result"] == result
    assert audit["request_digest"] == parsed_request.digest

    conflicting_before = _snapshot(database)
    second_backup = json.loads(
        run_cli("backup", "create", "--config", str(project["config_path"])).stdout
    )
    assert (backup_directory(project) / second_backup["name"]).is_file()
    conflicts = [
        _request(backup["name"], authorization="Different authorization with the same request ID."),
        _request(second_backup["name"]),
    ]
    for changed_intent in conflicts:
        conflict = _invoke_cli(project, changed_intent)
        assert conflict.returncode == 2
        assert "request_id" in json.loads(conflict.stdout)["reason"]
        assert _snapshot(database) == conflicting_before

    with sqlite3.connect(database) as connection:
        original_audit = connection.execute(
            "SELECT data FROM journal WHERE event='task_process_snapshot_migrated'"
        ).fetchone()[0]
        malformed = {**audit, "result": {"bogus": "accepted"}}
        connection.execute(
            "UPDATE journal SET data=? WHERE event='task_process_snapshot_migrated'",
            (json.dumps(malformed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    malformed_before = _snapshot(database)
    rejected_replay = _invoke_cli(project, _request(backup["name"]))
    assert rejected_replay.returncode == 2
    assert "audit" in json.loads(rejected_replay.stdout)["reason"].lower()
    assert _snapshot(database) == malformed_before
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE journal SET data=? WHERE event='task_process_snapshot_migrated'",
            (original_audit,),
        )

    replay = _invoke_cli(project, _request(backup["name"]))
    assert replay.returncode == 0, replay.stderr
    replayed = json.loads(replay.stdout)
    assert replayed == {**result, "replayed": True}
    assert _snapshot(database) == after


def test_claimed_task_is_migrated_without_changing_ownership(project):
    from poise.application.task_process_migration import TaskProcessMigrationCommands
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration
    from poise.modules.foundation.errors import PoiseError

    database, expected_metadata = _seed_legacy_tasks(project)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE tasks SET claimed_by=? WHERE id='0063'",
            ("owner-session-0063",),
        )
    backup = backup_commands(project).create()
    before = _snapshot(database)

    first = _invoke_cli(project, _request(backup["name"], request_id="claimed-0063"))

    assert first.returncode == 0, first.stdout + first.stderr
    result = json.loads(first.stdout)
    assert result["status"] == "migrated"
    assert result["replayed"] is False
    after = _snapshot(database)
    assert {key: value for key, value in after.items() if key not in {"tasks", "journal"}} == {
        key: value for key, value in before.items() if key not in {"tasks", "journal"}
    }
    before_tasks = {row[0]: row for row in before["tasks"]}
    after_tasks = {row[0]: row for row in after["tasks"]}
    for task_id in before_tasks:
        old = before_tasks[task_id]
        new = after_tasks[task_id]
        assert old[:7] == new[:7]
        old_metadata = json.loads(old[7])
        new_metadata = json.loads(new[7])
        if task_id in TASKS:
            old_process = old_metadata.pop("process")
            new_process = new_metadata.pop("process")
            assert new_process == {
                **old_process,
                "worktree_required": result["tasks"][TASKS.index(task_id)]["worktree_required"],
            }
        assert new_metadata == old_metadata
    assert after_tasks["0063"][4] == "owner-session-0063"
    assert len(after["journal"]) == len(before["journal"]) + 1

    replay = _invoke_cli(project, _request(backup["name"], request_id="claimed-0063"))
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert json.loads(replay.stdout) == {**result, "replayed": True}

    backup_commands(project).restore(backup["name"])
    before_drift = _snapshot(database)

    def replace_claim_after_preflight() -> None:
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE tasks SET claimed_by=? WHERE id='0063'",
                ("replacement-session",),
            )

    port = SqliteTaskProcessMigration(
        project["config_path"],
        clock=FakeClock(),
        after_preflight=replace_claim_after_preflight,
    )
    with pytest.raises(PoiseError, match="differs from the named backup after migration preflight"):
        TaskProcessMigrationCommands(port).migrate(
            _request(backup["name"], request_id="claimed-0063-drift")
        )

    after_drift = _snapshot(database)
    assert after_drift["journal"] == before_drift["journal"]
    assert all(
        "worktree_required" not in json.loads(row[7])["process"]
        for row in after_drift["tasks"]
        if row[0] in TASKS
    )
    assert {row[0]: row[4] for row in after_drift["tasks"]} == {
        **{row[0]: row[4] for row in before_drift["tasks"]},
        "0063": "replacement-session",
    }
    assert expected_metadata["0063"]["marker"] == {"preserve": "0063"}


def test_rolls_back_complete_batch_and_receipt_on_fault(project):
    from poise.application.task_process_migration import TaskProcessMigrationCommands
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

    database, _ = _seed_legacy_tasks(project)
    backup = backup_commands(project).create()
    before = _snapshot(database)
    with pytest.raises(TypeError):
        SqliteTaskProcessMigration(project["config_path"])

    def fail_after_first(task_id: str) -> None:
        if task_id == TASKS[0]:
            raise RuntimeError("injected migration failure")

    port = SqliteTaskProcessMigration(project["config_path"], clock=FakeClock(),
                                      after_update=fail_after_first)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        TaskProcessMigrationCommands(port).migrate(_request(backup["name"]))

    assert _snapshot(database) == before
    completed = TaskProcessMigrationCommands(
        SqliteTaskProcessMigration(project["config_path"], clock=FakeClock())
    ).migrate(_request(backup["name"]))
    assert completed["status"] == "migrated"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT at FROM journal WHERE event='task_process_snapshot_migrated'"
        ).fetchone()[0] == FIXED_NOW.isoformat()


def test_rejects_invalid_targets_and_conflicting_process_values(project):
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
    conflicting = deepcopy(good); conflicting[0]["metadata"]["process"]["worktree_required"] = False; variants.append(conflicting)
    mismatch = deepcopy(good); mismatch[0]["metadata"]["process"]["goal_type"] = "documentation"; variants.append(mismatch)
    invalid_goal = deepcopy(good); invalid_goal[0]["metadata"]["contract"]["goal_type"] = []; variants.append(invalid_goal)
    for rows in variants:
        with pytest.raises(DomainError):
            ProcessSnapshotMigration.plan(request, rows, configured)

    database, _ = _seed_legacy_tasks(project)
    with sqlite3.connect(database) as connection:
        original_rows = {
            row[0]: row
            for row in connection.execute(
                "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
                "FROM tasks ORDER BY id"
            )
        }

    def metadata_change(task_id: str, field: str, value: object) -> None:
        with sqlite3.connect(database) as connection:
            raw = connection.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            metadata = json.loads(raw)
            metadata["process"][field] = value
            connection.execute(
                "UPDATE tasks SET metadata=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")), task_id),
            )

    def execute(statement: str) -> None:
        with sqlite3.connect(database) as connection:
            connection.execute(statement)

    cases = [
        (
            "terminal",
            lambda: execute("UPDATE tasks SET status='completed' WHERE id='0074'"),
            "terminal",
        ),
        (
            "missing",
            lambda: execute("DELETE FROM tasks WHERE id='0074'"),
            "0074",
        ),
        (
            "conflicting",
            lambda: metadata_change("0078", "worktree_required", False),
            "worktree_required",
        ),
        (
            "goal-mismatch",
            lambda: metadata_change("0078", "goal_type", "documentation"),
            "goal_type",
        ),
    ]
    for label, mutate, reason in cases:
        mutate()
        created = json.loads(
            run_cli("backup", "create", "--config", str(project["config_path"])).stdout
        )
        before_case = _snapshot(database)
        result = _invoke_cli(
            project,
            _request(created["name"], request_id=f"reject-{label}"),
        )
        assert result.returncode == 2
        assert reason in json.loads(result.stdout)["reason"].lower()
        assert _snapshot(database) == before_case
        with sqlite3.connect(database) as connection:
            row = original_rows["0074" if label in {"terminal", "missing"} else "0078"]
            connection.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?,?)", row)


def test_stale_backup_or_live_row_drift_rejects_every_update(project):
    from poise.modules.foundation.errors import PoiseError
    from poise.modules.tasks.process_migration import MigrationRequest
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

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

    backup_commands(project).restore(backup["name"])
    before_interposed = _snapshot(database)

    def drift_after_preflight() -> None:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", (TASKS[1],))

    port = SqliteTaskProcessMigration(
        project["config_path"], clock=FakeClock(), after_preflight=drift_after_preflight
    )
    with pytest.raises(
        PoiseError,
        match="Task DB differs from the named backup after migration preflight",
    ):
        port.migrate(MigrationRequest.parse(_request(backup["name"])))
    drifted = _snapshot(database)
    expected_drifted = deepcopy(before_interposed)
    task_rows = list(expected_drifted["tasks"])
    row_index = next(index for index, item in enumerate(task_rows) if item[0] == TASKS[1])
    row = list(task_rows[row_index])
    row[5] += 1
    task_rows[row_index] = tuple(row)
    expected_drifted["tasks"] = task_rows
    assert drifted == expected_drifted


def test_cli_requires_exact_authorized_batch(project):
    from poise.modules.tasks.process_migration import MigrationRequest

    invalid = [
        {},
        _request("tasks-backup.sqlite", schema="unknown"),
        _request("../tasks-backup.sqlite"),
        _request("tasks-backup.sqlite", task_ids=["0077", "0077"]),
        _request("tasks-backup.sqlite", task_ids=list(TASKS[:-1])),
        _request("tasks-backup.sqlite", task_ids=[*TASKS, "CONTROL"]),
        _request("tasks-backup.sqlite", task_ids=["OTHER", *TASKS[1:]]),
        _request("tasks-backup.sqlite", authorization=""),
        {**_request("tasks-backup.sqlite"), "extra": True},
    ]
    for value in invalid:
        with pytest.raises(Exception):
            MigrationRequest.parse(value)

    result = run_cli("task-process-migrate", "--config", str(project["config_path"]))
    assert result.returncode == 2
    assert "traceback" not in result.stderr.lower()
