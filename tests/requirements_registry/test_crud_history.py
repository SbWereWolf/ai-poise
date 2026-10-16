"""Integration subject: CRUD/shared receipt namespace and immutable snapshot consumers."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import sqlite3

from conftest import write_json
from requirements_registry.snapshot_store import TaskRequirementsSnapshotStore
from requirements_registry.crud_support import (
    arrange, invoke, literal, snapshot,
)


def test_historical_apply_receipt_is_not_reinterpreted_as_command(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    history = literal("crud_history.json")
    body = history["apply_request"]
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with closing(sqlite3.connect(database)) as db:
        db.execute(
            "INSERT INTO requirement_requests(request_id,digest,response) VALUES(?,?,?)",
            ("historical", hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
             json.dumps(history["old_response"])),
        )
        db.commit()
    before = snapshot(database)
    code, response = invoke(config, {"operation": "apply", "input": body})
    assert code == 0 and response == history["replay_response"]
    assert snapshot(database) == before
    code, response = invoke(config, history["command_request"])
    assert code == 2 and response == history["collision_response"]
    assert snapshot(database) == before


def test_command_receipt_cannot_be_replayed_by_apply(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    faults = literal("crud_faults.json")
    history = literal("crud_history.json")
    code, response = invoke(config, faults["request"])
    assert code == 0 and response == faults["success"]
    before = snapshot(database)
    body = deepcopy(history["apply_request"])
    body["request_id"] = "fault"
    code, response = invoke(config, {"operation": "apply", "input": body})
    assert code == 2
    assert response == {
        "status": "rejected", "reason": "Requirements request_id was used for another request",
    }
    assert snapshot(database) == before


def test_live_crud_preserves_historical_task_snapshot_bytes(project, tmp_path):
    database, _, config = arrange(project, tmp_path)
    historical_db = project["root"] / project["cfg"]["paths"]["state"] / "historical-task.sqlite"
    historical = literal("crud_task_snapshot.json")
    with closing(sqlite3.connect(historical_db)) as db:
        db.execute(
            "CREATE TABLE task_requirements_snapshots("
            "task_id TEXT PRIMARY KEY,data TEXT NOT NULL,agreement TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO task_requirements_snapshots(task_id,data,agreement) VALUES(?,?,?)",
            ("historical-task", json.dumps(historical, ensure_ascii=False), "null"),
        )
        db.commit()
    config_value = json.loads(config.read_text(encoding="utf-8"))
    config_value["paths"]["database"] = "historical-task.sqlite"
    write_json(config, config_value)
    original_bytes = historical_db.read_bytes()
    fault = literal("crud_faults.json")
    code, response = invoke(config, fault["request"])
    assert code == 0 and response == fault["success"]
    assert historical_db.read_bytes() == original_bytes
    assert TaskRequirementsSnapshotStore(historical_db).read("historical-task") == historical
    assert historical_db.read_bytes() == original_bytes
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT text FROM requirements WHERE id=?", ("A3",)).fetchone() == ("Changed.",)
