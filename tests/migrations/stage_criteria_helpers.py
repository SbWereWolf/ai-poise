"""Historical test-owned state; no product parser normalizes the migration source."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from conftest import write_json
from tests.backups.helpers import backup_commands, live_database


class FixtureClock:
    def observe(self):
        from poise.modules.accounting.clock import ClockObservation
        return ClockObservation("2026-10-11T12:00:00+00:00", 1, "criteria-migration-fixture")


def identity(value):
    # Source identity observation, never the expected migrated output.
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def snapshot(database):
    with sqlite3.connect(database) as connection:
        return {
            "tasks": connection.execute(
                "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
                "FROM tasks ORDER BY id"
            ).fetchall(),
            "events": connection.execute(
                "SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq"
            ).fetchall(),
            "submissions": connection.execute(
                "SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq"
            ).fetchall(),
            "evidence": connection.execute(
                "SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id"
            ).fetchall(),
            "execution": connection.execute(
                "SELECT task_id,data,version FROM task_execution ORDER BY task_id"
            ).fetchall(),
            "journal": connection.execute(
                "SELECT seq,at,session_id,task_id,event,data FROM journal ORDER BY seq"
            ).fetchall(),
            "methods": connection.execute(
                "SELECT task_id,method_id,version,data FROM task_methods ORDER BY task_id,method_id"
            ).fetchall(),
            "proofs": connection.execute("SELECT task_id,data FROM task_proofs ORDER BY task_id").fetchall(),
            "content": connection.execute(
                "SELECT task_id,version,data FROM content_contracts ORDER BY task_id,version"
            ).fetchall(),
            "workflows": connection.execute("SELECT task_id,data FROM task_workflows ORDER BY task_id").fetchall(),
        }


def historical_project(project, *, include_newborn=False):
    from poise.infrastructure.goal_config import EditorSettings, FileGoalConfigRepository
    from poise.infrastructure.sqlite.database import Database

    database = live_database(project)
    store = Database(database, database.parent / "fixture-seed.lock", 2.0, 0.01)
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"
    historical = json.loads((fixtures / "historical-state.json").read_text(encoding="utf-8"))
    process = deepcopy(historical["process"])
    # Frozen owner-produced old state: future shared-fixture/schema edits cannot normalize it.
    assert all("purpose" not in stage and "criteria_authority" not in stage for stage in process["stages"])
    editor_document = json.loads((fixtures / "migration-editor.json").read_text(encoding="utf-8"))
    editor_path = write_json(project["root"] / "criteria-editor.json", editor_document)
    catalogue_document = json.loads((fixtures / "migration-catalogue.json").read_text(encoding="utf-8"))
    catalogue_path = write_json(project["root"] / "criteria-catalogue.json", catalogue_document)
    settings = EditorSettings(editor_path)
    repository = FileGoalConfigRepository(settings)
    historical_request = {"request_id": "historical-process-publication", "changes": []}
    with repository.edit("development") as edit:
        # Arrange literal OLD persistence through its real publication owner. The
        # target is initially absent; no current-domain parser generates this source.
        assert not settings.target("development").exists()
        published = edit.publish(historical_request, identity(historical_request), None, deepcopy(process), None)
    project["cfg"]["processes"]["development"] = editor_document["processes"]["development"]
    project["cfg"]["task_planning"] = {
        "catalogue": str(catalogue_path), "restart_revision_policy": {"reviewer": [], "user": []},
    }
    write_json(project["config_path"], project["cfg"])
    assert Path(published["config_path"]) == settings.target("development")
    with sqlite3.connect(project["root"] / editor_document["database"]) as connection:
        assert connection.execute(
            "SELECT revision FROM config_heads WHERE goal_type=?", ("development",),
        ).fetchone()[0] == published["revision"]
    contract = deepcopy(historical["contract"])
    # Explicit fixture binding of the runtime only, before the source is backed up.
    for method in contract["methods"]:
        assert method["argv"][0] == "$FIXTURE_PYTHON"
        method["argv"][0] = sys.executable
    requirements_snapshot = deepcopy(project["task"]["requirements_snapshot"])
    requirements_agreement = deepcopy(project["task"]["requirements_agreement"])
    task_ids = ("T1", "T2")
    with store.transaction() as connection:
        for index, task_id in enumerate(task_ids):
            saved_contract = {**deepcopy(contract), "id": task_id}
            metadata = {"contract": saved_contract, "process": deepcopy(process),
                        "goal": saved_contract["goal"], "historical_marker": task_id,
                        "config_hash": historical["config_hash"],
                        "sprint_id": None, "requirements_snapshot": deepcopy(requirements_snapshot),
                        "requirements_agreement": deepcopy(requirements_agreement)}
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, "available", 0, 1, None, 7 + index, None, json.dumps(metadata)),
            )
            connection.execute(
                "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                (task_id, 7 + index, "2026-10-01T12:00:00+00:00", '{"event":"historical_import"}'),
            )
            connection.execute(
                "INSERT INTO submissions(task_id,stage,iteration,digest,data) VALUES(?,?,?,?,?)",
                (task_id, "tests", 1, "saved-report-" + task_id, '{"report":"historical bytes"}'),
            )
            connection.execute(
                "INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,?,?)",
                ("proof-" + task_id, task_id, "tests", 1, '{"proof":"original evidence"}'),
            )
            connection.execute(
                "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
                (task_id, json.dumps(historical["execution"]), 3),
            )
            connection.execute("INSERT INTO task_workflows(task_id,data) VALUES(?,?)",
                               (task_id, json.dumps(historical["workflow"])))
            connection.execute("INSERT INTO task_proofs(task_id,data) VALUES(?,?)",
                               (task_id, json.dumps(historical["proof"])))
            connection.execute("INSERT INTO content_contracts(task_id,version,data) VALUES(?,?,?)",
                               (task_id, 1, json.dumps(historical["content_contract"])))
            for method in contract["methods"]:
                scheduled = [stage for stage, methods in contract["checks"].items() if method["id"] in methods]
                connection.execute(
                    "INSERT INTO task_methods(task_id,method_id,version,data) VALUES(?,?,?,?)",
                    (task_id, method["id"], 7 + index, json.dumps({"method": method, "stages": scheduled})),
                )
        if include_newborn:
            draft = deepcopy(contract)
            draft.pop("id")
            draft.pop("sprint_id")
            metadata = {
                "sprint_id": None, "goal": draft["goal"], "config_hash": "historical-config",
                "newborn": {"draft": draft, "ready": False}, "process": deepcopy(process),
            }
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("N1", "newborn", 0, 1, None, 0, None, json.dumps(metadata)),
            )
            created_event = connection.execute(
                "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                ("N1", 0, "2026-10-01T12:00:00+00:00",
                 '{"event":"created_newborn","stage":"newborn","iteration":1}'),
            ).lastrowid
    backup = backup_commands(project).create()
    kinds = ("design", "observation", "execution", "observation")
    stages = [
        {"stage_id": stage["id"], "purpose": purpose,
         "criteria_authority": {"propose": [], "decide": []}}
        for stage, purpose in zip(process["stages"], kinds, strict=True)
    ]
    mapping = [{"kind": "process_definition", "goal_type": "development",
                "expected_revision": published["revision"], "stages": stages}]
    for index, task_id in enumerate(task_ids):
        saved_contract = {**deepcopy(contract), "id": task_id}
        mapping.append({
            "kind": "executable_task", "task_id": task_id, "expected_version": 7 + index,
            "expected_process_digest": identity(process),
            "stages": [
                {**deepcopy(stage),
                 "criteria": {"requirements": [], "definition_of_ready": [], "definition_of_done": []},
                 "execution": {"state": "unknown", "source_revision": None,
                               "source_refs": [{"kind": "contract", "digest": identity(saved_contract)}]}}
                for stage in stages
            ],
        })
    if include_newborn:
        mapping.append({
            "kind": "newborn_task", "task_id": "N1", "expected_revision": 0,
            "expected_process_digest": identity(process),
            "stages": [
                {**deepcopy(stage),
                 "criteria": {"requirements": [], "definition_of_ready": [], "definition_of_done": []},
                 "execution": {"state": "unstarted", "source_revision": None,
                               "source_refs": [{"kind": "task_event", "id": created_event}]}}
                for stage in stages
            ],
        })
    packet = {
        "schema": "task-stage-criteria-migration-1", "request_id": "real-criteria-cutover",
        "backup_name": backup["name"], "mapping": mapping,
        "authorization": "Migrate the complete quiescent fixture inventory, preserving historical state.",
    }
    return database, packet, settings.target("development")


def assert_history_preserved(before, after):
    for table in ("events", "submissions", "evidence", "execution", "methods", "proofs", "content"):
        assert after[table] == before[table]
    assert len(after["workflows"]) == len(before["workflows"])
    for (old_task, old_raw), (new_task, new_raw) in zip(before["workflows"], after["workflows"], strict=True):
        assert new_task == old_task
        for key, value in json.loads(old_raw).items():
            assert json.loads(new_raw)[key] == value
    assert len(after["tasks"]) == len(before["tasks"])
    for old, new in zip(before["tasks"], after["tasks"], strict=True):
        assert new[:7] == old[:7]
        old_metadata, new_metadata = json.loads(old[7]), json.loads(new[7])
        for key, value in old_metadata.items():
            if key not in ("process", "contract", "newborn"):
                assert new_metadata[key] == value
        stripped_process = deepcopy(new_metadata["process"])
        for stage in stripped_process["stages"]:
            stage.pop("purpose")
            stage.pop("criteria_authority")
        assert stripped_process == old_metadata["process"]
        if old[1] == "newborn":
            assert new_metadata["newborn"]["ready"] == old_metadata["newborn"]["ready"]
            stripped_contract = deepcopy(new_metadata["newborn"]["draft"])
            old_contract = old_metadata["newborn"]["draft"]
        else:
            stripped_contract = deepcopy(new_metadata["contract"])
            old_contract = old_metadata["contract"]
        for stage in stripped_contract["stage_contracts"]:
            assert stage.pop("criteria") == {
                "requirements": [], "definition_of_ready": [], "definition_of_done": [],
            }
        assert stripped_contract == old_contract
