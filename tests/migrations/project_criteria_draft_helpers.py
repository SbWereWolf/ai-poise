"""Transparent disposable persistence for draft migration integration tests.

No production factory or transformer generates expected values. Only pytest's
temporary demo installation is written; the operator project is never opened.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from tests.backups.helpers import FIXED_NOW, backup_commands
from tests.migrations.project_criteria_helpers import historical_project_owned


ROOT = Path(__file__).resolve().parents[2]
DATA = json.loads((ROOT / "fixtures/stage-criteria/project-draft-sources.json").read_text())


def metadata_digest(value):
    """Independent observation of the explicitly agreed canonical SHA contract."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def rows(case):
    with sqlite3.connect(case["database"]) as connection:
        return {row[0]: row for row in connection.execute(
            "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
            "FROM tasks ORDER BY id")}


def replace_metadata(case, task_id, metadata):
    raw = json.dumps(metadata, ensure_ascii=False, indent=3) + "\n"
    with sqlite3.connect(case["database"]) as connection:
        connection.execute("UPDATE tasks SET metadata=? WHERE id=?", (raw, task_id))


def fresh_backup(case):
    case["backup_counter"] = case.get("backup_counter", 0) + 1
    commands = backup_commands(case["project"])
    commands.clock = lambda: FIXED_NOW + timedelta(seconds=case["backup_counter"])
    case["packet"]["backup_name"] = commands.create()["name"]


def resource(case, task_id):
    return next(item for item in case["packet"]["mapping"] if item.get("task_id") == task_id)


def bind_resource(case, task_id):
    row = rows(case)[task_id]
    resource(case, task_id)["expected_revision"] = row[5]
    resource(case, task_id)["expected_metadata_digest"] = metadata_digest(json.loads(row[7]))


def draft_case(project, mode="mixed"):
    assert project["cfg"]["project"] == "demo"
    case = historical_project_owned(project)
    source_process = json.loads((ROOT / "fixtures/stage-criteria/historical-state.json").read_text())["process"]
    selected = ["U1"] if mode == "untyped" else ["P1"] if mode == "typed" else ["U1", "P1"]
    for task_id in selected:
        metadata = deepcopy(DATA["untyped" if task_id == "U1" else "typed_partial"])
        if task_id == "P1":
            metadata["process"] = deepcopy(source_process)
        raw = json.dumps(metadata, ensure_ascii=False, indent=3) + "\n"
        version = 3 if task_id == "U1" else 5
        with sqlite3.connect(case["database"]) as connection:
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, "newborn", 0, 1, "preserved-draft-owner-" + task_id, version, None, raw))
            connection.execute("INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                               (task_id, version, "2026-10-01T12:00:00+00:00",
                                '{"event":"created_newborn","stage":"newborn","iteration":1}'))
        case["packet"]["mapping"].append({
            "kind": "newborn_draft", "task_id": task_id, "expected_revision": version,
            "expected_metadata_digest": metadata_digest(metadata),
            "stages": [] if task_id == "U1" else deepcopy(DATA["expected_stages"]),
        })
    case["draft_ids"] = selected
    fresh_backup(case)
    return case


def assert_drafts_preserved(case, before, result):
    current = rows(case)
    for task_id in case["draft_ids"]:
        old, new = before[task_id], current[task_id]
        assert new[:7] == old[:7]
        source, actual = json.loads(old[7]), json.loads(new[7])
        if task_id == "U1":
            assert new[7] == old[7], "Untyped metadata TEXT must remain byte-for-byte equal."
        else:
            process = deepcopy(actual.pop("process"))
            prior_process = deepcopy(source.pop("process"))
            assert actual == source
            assert "stage_contracts" not in actual["newborn"]["draft"]
            assert "stage_criteria" not in actual
            assert [{"stage_id": s["id"], "purpose": s["purpose"],
                     "criteria_authority": s["criteria_authority"]}
                    for s in process["stages"]] == DATA["expected_stages"]
            for stage in process["stages"]:
                stage.pop("purpose")
                stage.pop("criteria_authority")
            assert process == prior_process
        summary = next(item for item in result["resources"] if item.get("task_id") == task_id)
        assert set(summary) == {"kind", "task_id", "revision", "old_digest", "new_digest"}
        assert summary["kind"] == "newborn_draft"
        assert summary["task_id"] == task_id
        assert summary["revision"] == old[5]
        assert summary["old_digest"] == metadata_digest(json.loads(old[7]))
        assert summary["new_digest"] == metadata_digest(json.loads(new[7]))


def assert_contextual_refusal(code, response, stderr, context, status="rejected"):
    assert code == 2, (response, stderr)
    assert response.get("status") == status, (response, stderr)
    reason = response.get("reason", "").lower()
    assert "unknown migration resource kind" not in reason, response
    assert all(word in reason for word in context), response
    assert "Traceback" not in stderr
