from copy import deepcopy
import json
import sqlite3

from tests.backups.helpers import backup_directory, configured_path, json_output, run_cli
from conftest import Poise, write_json


def test_public_migration_removes_route_count_limits_without_losing_task_state(project):
    runtime = Poise(project["config_path"], "S1")
    initial = runtime.bootstrap(task_file=project["task_path"])
    process_path = project["root"] / "config/processes/development.json"
    legacy_process = json.loads(process_path.read_text(encoding="utf-8"))
    legacy_process["route"].update(max_transitions=40, max_stage_visits=8)
    write_json(process_path, legacy_process)

    database = configured_path(project["config_path"], "database")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id='T1'"
        ).fetchone()
        metadata = json.loads(row[-1])
        legacy_metadata = deepcopy(metadata)
        legacy_metadata["process"]["route"].update(max_transitions=40, max_stage_visits=8)
        connection.execute(
            "UPDATE tasks SET metadata=? WHERE id='T1'",
            (json.dumps(legacy_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
        workflow_before = connection.execute(
            "SELECT data FROM task_workflows WHERE task_id='T1'"
        ).fetchone()[0]

    backup = json_output(run_cli("backup", "create", "--config", str(project["config_path"])))
    assert backup["success"] is True
    assert (backup_directory(project) / backup["name"]).is_file()

    migrated = json_output(run_cli("route-migrate", "--config", str(project["config_path"])))

    assert migrated == {"status": "migrated", "processes": 1, "task_snapshots": 1}
    assert json.loads(process_path.read_text(encoding="utf-8"))["route"] == {"entry": "tests"}
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id='T1'"
        ).fetchone()
        workflow_after = connection.execute(
            "SELECT data FROM task_workflows WHERE task_id='T1'"
        ).fetchone()[0]
    migrated_metadata = json.loads(after[-1])
    expected_metadata = deepcopy(legacy_metadata)
    expected_metadata["process"]["route"] = {"entry": "tests"}
    assert after[:-1] == row[:-1]
    assert migrated_metadata == expected_metadata
    assert workflow_after == workflow_before
    assert Poise(project["config_path"], "S1").show()["stage"] == initial["stage"]

    replay = json_output(run_cli("route-migrate", "--config", str(project["config_path"])))
    assert replay == {"status": "migrated", "processes": 0, "task_snapshots": 0}
