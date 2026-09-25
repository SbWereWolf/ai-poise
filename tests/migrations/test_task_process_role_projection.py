"""Explicit atomic projection of configured stage roles into legacy newborn Task snapshots."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import Poise
from tests.backups.helpers import backup_commands, backup_directory


SCHEMA = "task-process-role-migration-1"


def process(build_role="author", inspect_role="assayer"):
    return {
        "goal_type": "development",
        "worktree_required": True,
        "route": {"entry": "build"},
        "benefit": {"git_categories": [], "sections": ["report"]},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "stages": [
            {
                "id": "build",
                "role": build_role,
                "handler": "produce",
                "transitions": {"complete": "inspect"},
                "rework_targets": ["build"],
                "read_only": False,
                "allowed_paths": ["src/**"],
                "instruction": "Produce.",
                "normalization": "strip",
                "sections": {"report": "Result"},
                "required_sections": ["report"],
                "artifact_requirements": [],
            },
            {
                "id": "inspect",
                "role": inspect_role,
                "handler": "inspect",
                "transitions": {"clear": None, "changes_requested": "build"},
                "rework_targets": ["build"],
                "read_only": True,
                "allowed_paths": [],
                "instruction": "Inspect.",
                "normalization": "strip",
                "sections": {"report": "Review"},
                "required_sections": ["report"],
                "artifact_requirements": [],
            },
        ],
    }


def without_roles(value):
    result = deepcopy(value)
    for stage in result["stages"]:
        stage.pop("role", None)
    return result


def row(task_id, configured, *, sprint="S", status="newborn", claimed_by=None, ready=False):
    return {
        "id": task_id,
        "status": status,
        "claimed_by": claimed_by,
        "metadata": {
            "sprint_id": sprint,
            "goal": task_id,
            "config_hash": "config",
            "newborn": {"draft": {"goal_type": configured["goal_type"]}, "ready": ready},
            "process": without_roles(configured),
        },
    }


def request(backup_name="tasks-backup.sqlite", **changes):
    value = {
        "schema": SCHEMA,
        "request_id": "project-configured-roles-1",
        "backup_name": backup_name,
        "sprint_id": "S",
        "task_ids": ["R1", "R2"],
        "role_source": "configured_process",
        "authorization": "User authorized the exact draft Sprint role projection.",
    }
    value.update(changes)
    return value


def test_role_projection_request_is_generic_and_not_bound_to_executor_or_known_task_ids():
    from poise.modules.tasks.process_migration import MigrationRequest

    parsed = MigrationRequest.parse(request(task_ids=["ANY-2", "ANY-1"], sprint_id="SPRINT-X"))

    assert parsed.schema == SCHEMA
    assert parsed.task_ids == ("ANY-2", "ANY-1")
    assert parsed.sprint_id == "SPRINT-X"
    assert parsed.role_source == "configured_process"


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"task_ids": []}, "task_ids"),
        ({"task_ids": ["R1", "R1"]}, "unique"),
        ({"task_ids": ["R1", ""]}, "nonempty"),
        ({"sprint_id": ""}, "sprint_id"),
        ({"role_source": "handler"}, "configured_process"),
    ],
)
def test_role_projection_request_rejects_ambiguous_scope_or_inferred_role_source(changes, match):
    from poise.modules.foundation.errors import DomainError
    from poise.modules.tasks.process_migration import MigrationRequest

    with pytest.raises(DomainError, match=match):
        MigrationRequest.parse(request(**changes))


def test_domain_projects_exact_configured_roles_without_changing_any_other_process_field():
    from poise.modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration

    configured = process("developer", "assayer")
    legacy = without_roles(configured)
    parsed = MigrationRequest.parse(request())
    plan = ProcessSnapshotMigration.plan(
        parsed,
        [row("R1", configured), row("R2", configured)],
        {"development": configured},
    )

    assert [item.task_id for item in plan.updates] == ["R1", "R2"]
    for update in plan.updates:
        assert without_roles(update.process) == legacy
        assert [(stage["id"], stage["role"]) for stage in update.process["stages"]] == [
            ("build", "developer"),
            ("inspect", "assayer"),
        ]
        assert update.stage_roles == (
            ("build", "developer"),
            ("inspect", "assayer"),
        )


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda rows: rows[0].update(status="available"), "newborn"),
        (lambda rows: rows[0].update(claimed_by="owner"), "unclaimed"),
        (lambda rows: rows[0]["metadata"].update(sprint_id="OTHER"), "Sprint"),
        (lambda rows: rows[0]["metadata"]["newborn"].update(ready=True), "not ready"),
        (lambda rows: rows[0]["metadata"]["process"]["stages"][0].update(role="legacy"), "already has role"),
    ],
)
def test_domain_rejects_unsafe_legacy_task_state(mutate, match):
    from poise.modules.foundation.errors import DomainError
    from poise.modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration

    configured = process()
    rows = [row("R1", configured), row("R2", configured)]
    mutate(rows)
    with pytest.raises(DomainError, match=match):
        ProcessSnapshotMigration.plan(
            MigrationRequest.parse(request()), rows, {"development": configured}
        )


def test_domain_rejects_config_drift_beyond_role_fields():
    from poise.modules.foundation.errors import DomainError
    from poise.modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration

    configured = process()
    rows = [row("R1", configured), row("R2", configured)]
    configured["stages"][0]["instruction"] = "Changed process semantics."
    with pytest.raises(DomainError, match="differs.*role|role.*only"):
        ProcessSnapshotMigration.plan(
            MigrationRequest.parse(request()), rows, {"development": configured}
        )


def _database(project):
    cfg = json.loads(project["config_path"].read_text())
    state = Path(cfg["paths"]["state"])
    if not state.is_absolute():
        state = project["config_path"].parent / state
    return state / cfg["paths"]["database"]


def _configured(project, goal_type="development"):
    cfg = json.loads(project["config_path"].read_text())
    return json.loads((project["config_path"].parent / cfg["processes"][goal_type]).read_text())


def _seed_legacy_newborns(project):
    from poise.infrastructure.sqlite.database import Database

    Poise(project["config_path"], "schema-initializer")
    configured = _configured(project)
    database = _database(project)
    cfg = json.loads(project["config_path"].read_text())
    db = Database(database, database.parent / cfg["paths"]["lock"], 2.0, 0.01)
    legacy = without_roles(configured)
    with db.transaction() as connection:
        for task_id in ("R1", "R2"):
            metadata = {
                "sprint_id": "S",
                "goal": task_id,
                "config_hash": "legacy-config",
                "newborn": {"draft": {"goal_type": "development"}, "ready": False},
                "process": deepcopy(legacy),
                "preserve": {"task": task_id},
            }
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, "newborn", 0, 1, None, 7, None,
                 json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            )
    return database, legacy, configured


def _invoke(project, packet):
    env = os.environ.copy()
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "poise", "task-process-migrate", "--config", str(project["config_path"])],
        cwd=root,
        env=env,
        input=json.dumps(packet),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_migrates_exact_newborn_batch_atomically_and_replays(project):
    database, legacy, configured = _seed_legacy_newborns(project)
    backup = backup_commands(project).create()
    backup_path = backup_directory(project) / backup["name"]
    backup_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()

    first = _invoke(project, request(backup["name"]))

    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["status"] == "migrated" and result["replayed"] is False
    assert [item["task_id"] for item in result["tasks"]] == ["R1", "R2"]
    assert all(item["sprint_id"] == "S" for item in result["tasks"])
    assert all(item["stage_roles"] == [
        {"stage_id": stage["id"], "role": stage["role"]} for stage in configured["stages"]
    ] for item in result["tasks"])
    assert hashlib.sha256(backup_path.read_bytes()).hexdigest() == backup_hash

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id,status,claimed_by,version,metadata FROM tasks ORDER BY id"
        ).fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("R1", "newborn", None, 7), ("R2", "newborn", None, 7)
    ]
    for task_id, _, _, _, raw in rows:
        metadata = json.loads(raw)
        assert metadata["preserve"] == {"task": task_id}
        assert metadata["newborn"] == {"draft": {"goal_type": "development"}, "ready": False}
        assert without_roles(metadata["process"]) == legacy
        assert metadata["process"] == configured

    replay = _invoke(project, request(backup["name"]))
    assert replay.returncode == 0, replay.stderr
    second = json.loads(replay.stdout)
    assert second == {**result, "replayed": True}
