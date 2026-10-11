"""Independent new-wire/CLI contracts, not a replacement migration engine."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from poise.application.task_process_migration import TaskProcessMigrationCommands
from poise.interfaces import task_process_migration
from poise.modules.foundation.errors import PoiseError
from conftest import write_json
from conftest import WorkPoise
from batch.helpers import request
from poise.application.work import WorkTools
from tests.runtime_services.stage_criteria_helpers import show
from tests.migrations.stage_criteria_helpers import (
    FixtureClock, assert_history_preserved, historical_project, snapshot,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingPort:
    """Observe the real application parser's dispatch without filesystem effects."""

    def __init__(self, response):
        self.requests = []
        self.response = response

    def migrate(self, request):
        self.requests.append(request)
        return deepcopy(self.response)


def test_new_single_apply_request_reaches_existing_migration_port():
    packet = fixture("migration-request.json")
    expected = fixture("migration-success-response.json")
    port = RecordingPort(expected)
    observed = TaskProcessMigrationCommands(port).migrate(packet)
    assert observed == expected
    assert len(port.requests) == 1
    assert port.requests[0].schema == "task-stage-criteria-migration-1"
    assert port.requests[0].request_id == "migrate-fixture-criteria"
    assert packet == fixture("migration-request.json")


@pytest.mark.parametrize("fault", [
    "removed_inventory", "unknown_field", "empty_mapping", "duplicate_resource", "empty_authorization",
])
def test_invalid_mapping_is_rejected_before_port_dispatch(fault):
    packet = fixture("migration-request.json")
    if fault == "removed_inventory":
        packet["expected_inventory_digest"] = "0" * 64
    elif fault == "unknown_field":
        packet["phase"] = "prepare"
    elif fault == "empty_mapping":
        packet["mapping"] = []
    elif fault == "duplicate_resource":
        packet["mapping"].append(deepcopy(packet["mapping"][0]))
    else:
        packet["authorization"] = ""
    port = RecordingPort({})
    with pytest.raises(PoiseError):
        TaskProcessMigrationCommands(port).migrate(packet)
    assert port.requests == []


def test_cli_partial_publication_returns_exact_recovery_envelope_and_exit_two(monkeypatch, tmp_path):
    expected = fixture("migration-recovery-response.json")
    port = RecordingPort(expected)
    monkeypatch.setattr(task_process_migration, "task_process_migration_tools", lambda path: port)
    output = io.StringIO()
    code = task_process_migration.execute(
        tmp_path / "project.json",
        io.BytesIO(json.dumps(fixture("migration-request.json")).encode("utf-8")),
        output,
    )
    assert json.loads(output.getvalue()) == expected
    assert code == 2
    assert len(port.requests) == 1
    assert "phase" not in json.loads(output.getvalue())
    assert "prepared" not in output.getvalue()


def invoke_cli(project, packet):
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    return subprocess.run(
        [sys.executable, "-B", "-m", "poise", "task-process-migrate", "--config", str(project["config_path"])],
        input=json.dumps(packet), text=True, capture_output=True, check=False,
        cwd=root, env=environment,
    )


def test_real_cli_migrates_complete_historical_inventory_and_exact_replay_preserves_data(project):
    database, packet, process_path = historical_project(project)
    before = snapshot(database)
    original_process = json.loads(process_path.read_text(encoding="utf-8"))
    command = invoke_cli(project, packet)
    assert command.returncode == 0, (command.stdout, command.stderr)
    response = json.loads(command.stdout)
    assert set(response) == {"status", "request_id", "resources", "receipt", "replayed"}
    assert response["status"] == "migrated"
    assert response["request_id"] == packet["request_id"]
    assert response["replayed"] is False
    assert isinstance(response["receipt"], str) and response["receipt"]
    assert [(r["kind"], r.get("task_id", r.get("goal_type"))) for r in response["resources"]] == [
        ("process_definition", "development"), ("executable_task", "T1"), ("executable_task", "T2"),
    ]
    for resource, mapping in zip(response["resources"], packet["mapping"], strict=True):
        assert resource["old_digest"] != resource["new_digest"]
        if resource["kind"] == "process_definition":
            assert resource["old_revision"] == mapping["expected_revision"]
        else:
            assert resource["version"] == mapping["expected_version"]
    migrated_process = json.loads(process_path.read_text(encoding="utf-8"))
    assert [s["purpose"] for s in migrated_process["stages"]] == [
        "design", "observation", "execution", "observation",
    ]
    for stage in migrated_process["stages"]:
        assert stage.pop("criteria_authority") == {"propose": [], "decide": []}
        stage.pop("purpose")
    assert migrated_process == original_process
    after = snapshot(database)
    assert_history_preserved(before, after)
    replay = invoke_cli(project, packet)
    assert replay.returncode == 0, (replay.stdout, replay.stderr)
    assert json.loads(replay.stdout) == {**response, "replayed": True}
    assert snapshot(database) == after
    changed = deepcopy(packet)
    changed["authorization"] = "A different intent cannot reuse this request identity."
    conflict = invoke_cli(project, changed)
    assert conflict.returncode == 2
    assert json.loads(conflict.stdout)["status"] == "rejected"
    assert snapshot(database) == after
    tools = WorkTools(WorkPoise(project["config_path"], "historical-continuation"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert context["task"] == "T1"
    current = show(tools, "implementation")
    assert current["execution"]["state"] == "unknown"
    assert current["execution"]["bound_revision"] is not None
    assert current["source"]["kind"] == "historical"
    assert current["source"]["migration_receipt"] == response["receipt"]
    assert current["proposals"] == []
    assert current["criteria"] == {"requirements": [], "definition_of_ready": [], "definition_of_done": []}


def test_real_migration_preserves_newborn_draft_revision_and_genuine_unstarted_provenance(project):
    database, packet, process_path = historical_project(project, include_newborn=True)
    before = snapshot(database)
    command = invoke_cli(project, packet)
    assert command.returncode == 0, (command.stdout, command.stderr)
    response = json.loads(command.stdout)
    assert response["status"] == "migrated"
    newborn = response["resources"][-1]
    assert set(newborn) == {"kind", "task_id", "old_digest", "new_digest", "revision"}
    assert newborn["kind"] == "newborn_task"
    assert newborn["task_id"] == "N1"
    assert newborn["revision"] == 0
    assert newborn["old_digest"] != newborn["new_digest"]
    after = snapshot(database)
    assert_history_preserved(before, after)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM task_execution WHERE task_id=?", ("N1",),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["missing_resource", "extra_resource", "stale_version", "missing_stage", "false_start"])
def test_real_migration_preflight_rejects_incomplete_or_false_mapping_before_any_write(project, fault):
    database, packet, process_path = historical_project(project)
    if fault == "missing_resource":
        packet["mapping"].pop()
    elif fault == "extra_resource":
        item = deepcopy(packet["mapping"][-1])
        item["task_id"] = "NOT-PRESENT"
        packet["mapping"].append(item)
    elif fault == "stale_version":
        packet["mapping"][-1]["expected_version"] += 1
    elif fault == "missing_stage":
        packet["mapping"][-1]["stages"].pop()
    else:
        packet["mapping"][-1]["stages"][2]["execution"]["state"] = "started"
    before, process_bytes = snapshot(database), process_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected"
    assert isinstance(response["reason"], str) and response["reason"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == process_bytes


@pytest.mark.parametrize("fault", [
    "absent_binding", "missing_settings", "malformed_settings", "unregistered_goal",
    "different_registered_target", "no_managed_head", "missing_owner_database",
    "stale_managed_revision", "live_head_drift", "pending_publication",
])
def test_configured_publication_owner_must_match_exact_project_target_and_managed_head(project, fault):
    from poise.infrastructure.goal_config import EditorSettings, FileGoalConfigRepository

    database, packet, process_path = historical_project(project)
    path = project["root"] / "criteria-editor.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    owner_database = project["root"] / settings["database"]
    if fault == "absent_binding":
        del project["cfg"]["task_planning"]
        write_json(project["config_path"], project["cfg"])
    elif fault == "missing_settings":
        write_json(project["root"] / "criteria-catalogue.json", {
            "schema": "process-catalogue-1", "editor": "missing-editor.json",
            "task_templates": {}, "max_items": 1000,
        })
    elif fault == "malformed_settings":
        write_json(path, {"schema": "not-editor-settings"})
    elif fault == "unregistered_goal":
        settings["processes"] = {"other": "config/processes/managed-other.json"}
        write_json(path, settings)
    elif fault == "different_registered_target":
        twin = "config/processes/unmanaged-twin.json"
        write_json(project["root"] / twin, json.loads(process_path.read_text(encoding="utf-8")))
        project["cfg"]["processes"]["development"] = twin
        write_json(project["config_path"], project["cfg"])
        configured = project["root"] / project["cfg"]["processes"]["development"]
        assert json.loads(configured.read_text(encoding="utf-8")) == json.loads(process_path.read_text(encoding="utf-8"))
    elif fault in ("no_managed_head", "missing_owner_database"):
        settings["database"] = "state/unmanaged-config.sqlite"
        write_json(path, settings)
        owner_database = project["root"] / settings["database"]
        if fault == "no_managed_head":
            with FileGoalConfigRepository(EditorSettings(path)).edit("development", recover=False):
                pass  # Real owner initializes its storage; no managed head is manufactured.
    elif fault == "stale_managed_revision":
        packet["mapping"][0]["expected_revision"] = "0" * 64
    elif fault == "live_head_drift":
        document = json.loads(process_path.read_text(encoding="utf-8"))
        document["stages"][0]["instruction"] = "An external writer changed the source after its managed publication."
        write_json(process_path, document)
    else:
        # Test-owned pending journal input. Keep the genuine head/receipt intact;
        # copying its candidate is arrangement, not a generated migration oracle.
        with sqlite3.connect(owner_database) as connection:
            row = connection.execute(
                "SELECT request_digest,goal_type,candidate,receipt FROM config_operations WHERE request_id=?",
                ("historical-process-publication",),
            ).fetchone()
            receipt = json.loads(row[3])
            receipt["request_id"] = "pending-fixture-publication"
            connection.execute(
                "INSERT INTO config_operations(request_id,request_digest,goal_type,source_revision,candidate,receipt,phase) "
                "VALUES(?,?,?,?,?,?,?)",
                ("pending-fixture-publication", row[0], row[1], packet["mapping"][0]["expected_revision"],
                 row[2], json.dumps(receipt), "pending"),
            )

    def owner_rows():
        if not owner_database.exists():
            return None
        with sqlite3.connect(f"file:{owner_database}?mode=ro", uri=True) as connection:
            return (
                connection.execute("SELECT goal_type,revision FROM config_heads ORDER BY goal_type").fetchall(),
                connection.execute(
                    "SELECT request_id,request_digest,goal_type,source_revision,candidate,receipt,phase "
                    "FROM config_operations ORDER BY request_id"
                ).fetchall(),
            )

    owner_before = owner_rows()
    before, published = snapshot(database), process_path.read_bytes()
    configured_path = project["root"] / project["cfg"]["processes"]["development"]
    configured_bytes = configured_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected"
    assert response["reason"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == published
    assert configured_path.read_bytes() == configured_bytes
    assert owner_rows() == owner_before


def test_real_migration_rechecks_drift_after_preflight_without_overwriting_the_writer(project):
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

    database, packet, process_path = historical_project(project)
    before = snapshot(database)
    process_bytes = process_path.read_bytes()

    def drift():
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T2",))

    port = SqliteTaskProcessMigration(project["config_path"], FixtureClock(), after_preflight=drift)
    with pytest.raises(PoiseError):
        TaskProcessMigrationCommands(port).migrate(packet)
    after = snapshot(database)
    expected = deepcopy(before)
    expected["tasks"][-1] = (*before["tasks"][-1][:5], before["tasks"][-1][5] + 1, *before["tasks"][-1][6:])
    assert after == expected
    assert process_path.read_bytes() == process_bytes


def test_published_process_and_rolled_back_tasks_have_truthful_recovery_and_resumable_receipt(project):
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

    database, packet, process_path = historical_project(project)
    before = snapshot(database)
    calls = []

    def fail_after_update(task_id):
        calls.append(task_id)
        raise OSError("Fixture failure after Task update, before transaction commit.")

    failing = SqliteTaskProcessMigration(project["config_path"], FixtureClock(), after_update=fail_after_update)
    response = TaskProcessMigrationCommands(failing).migrate(packet)
    assert calls == ["T1"]
    assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
    assert response["status"] == "recovery_required"
    assert response["request_id"] == packet["request_id"]
    assert response["reason"]
    assert [(r["kind"], r.get("goal_type")) for r in response["resources"]] == [
        ("process_definition", "development"),
    ]
    partial = snapshot(database)
    for table in ("tasks", "events", "submissions", "evidence", "execution"):
        assert partial[table] == before[table]
    published = process_path.read_bytes()
    assert [s["purpose"] for s in json.loads(published)["stages"]] == [
        "design", "observation", "execution", "observation",
    ]
    completed_calls = []
    recovered = TaskProcessMigrationCommands(SqliteTaskProcessMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert recovered["status"] == "migrated"
    assert recovered["receipt"] == response["receipt"]
    assert completed_calls == ["T1", "T2"]
    assert recovered["resources"][:1] == response["resources"]
    assert process_path.read_bytes() == published
    final = snapshot(database)
    assert_history_preserved(before, final)
    replay = TaskProcessMigrationCommands(SqliteTaskProcessMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert replay == {**recovered, "replayed": True}
    assert completed_calls == ["T1", "T2"]
    assert snapshot(database) == final


@pytest.mark.parametrize("schema,task_ids", [
    ("task-process-migration-1", ["0077", "0081", "0079", "0078", "0074", "0063"]),
    ("task-process-migration-2", ["0082"]),
    ("task-process-migration-3", ["0082"]),
    ("task-process-migration-4", ["ERP-HARNESS-COMPARISON-PLAN"]),
])
def test_historical_migration_schemas_keep_their_exact_request_contract(schema, task_ids):
    from poise.modules.tasks.process_migration import MigrationRequest

    packet = {"schema": schema, "request_id": "preserve-historical-wire", "backup_name": "fixture.sqlite",
              "task_ids": task_ids, "authorization": "Preserve the existing schema contract."}
    observed = MigrationRequest.parse(packet)
    assert observed.schema == schema
    assert list(observed.task_ids) == task_ids
    changed = {**packet, "mapping": []}
    with pytest.raises(PoiseError):
        MigrationRequest.parse(changed)
