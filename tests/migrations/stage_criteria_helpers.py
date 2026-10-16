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
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
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


def historical_project(project, *, include_newborn=False, workflow_source=None,
                       contract_source=None, create_backup=True):
    from poise.infrastructure.goal_config import EditorSettings, FileGoalConfigRepository
    from poise.infrastructure.sqlite.database import Database

    database = live_database(project)
    store = Database(database, database.parent / "fixture-seed.lock", 2.0, 0.01)
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"
    historical = json.loads((fixtures / "historical-state.json").read_text(encoding="utf-8"))
    workflow = deepcopy(historical["process"] if workflow_source is None else workflow_source)
    # Frozen owner-produced old state: future shared-fixture/schema edits cannot normalize it.
    assert all("purpose" not in stage and "criteria_authority" not in stage for stage in workflow["stages"])
    editor_document = json.loads((fixtures / "migration-editor.json").read_text(encoding="utf-8"))
    editor_document['workflows'] = editor_document.pop('processes')
    editor_path = write_json(project["root"] / "criteria-editor.json", editor_document)
    catalogue_document = json.loads((fixtures / "migration-catalogue.json").read_text(encoding="utf-8"))
    catalogue_document['schema'] = 'workflow-catalogue-1'
    catalogue_path = write_json(project["root"] / "criteria-catalogue.json", catalogue_document)
    settings = EditorSettings(editor_path)
    repository = FileGoalConfigRepository(settings)
    historical_request = {"request_id": "historical-process-publication", "changes": []}
    with repository.edit("development") as edit:
        # Arrange literal OLD persistence through its real publication owner. The
        # target is initially absent; no current-domain parser generates this source.
        assert not settings.target("development").exists()
        published = edit.publish(historical_request, identity(historical_request), None, deepcopy(workflow), None)
    project["cfg"]["workflows"]["development"] = editor_document["workflows"]["development"]
    project["cfg"]["task_planning"] = {
        "catalogue": str(catalogue_path), "restart_revision_policy": {"reviewer": [], "user": []},
    }
    write_json(project["config_path"], project["cfg"])
    assert Path(published["config_path"]) == settings.target("development")
    with sqlite3.connect(project["root"] / editor_document["database"]) as connection:
        assert connection.execute(
            "SELECT revision FROM config_heads WHERE goal_type=?", ("development",),
        ).fetchone()[0] == published["revision"]
    contract = deepcopy(historical["contract"] if contract_source is None else contract_source)
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
            metadata = {"contract": saved_contract, "workflow": deepcopy(workflow),
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
                "newborn": {"draft": draft, "ready": False}, "workflow": deepcopy(workflow),
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
    backup = backup_commands(project).create() if create_backup else None
    kinds = ("design", "observation", "execution", "observation")
    stages = [
        {"stage_id": stage["id"], "purpose": purpose,
         "criteria_authority": {"propose": [], "decide": []}}
        for stage, purpose in zip(workflow["stages"], kinds, strict=True)
    ]
    mapping = [{"kind": "workflow_definition", "goal_type": "development",
                "expected_revision": published["revision"], "stages": stages}]
    for index, task_id in enumerate(task_ids):
        saved_contract = {**deepcopy(contract), "id": task_id}
        mapping.append({
            "kind": "executable_task", "task_id": task_id, "expected_version": 7 + index,
            "expected_workflow_digest": identity(workflow),
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
            "expected_workflow_digest": identity(workflow),
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
        "backup_name": backup["name"] if backup is not None else None, "mapping": mapping,
        "authorization": "Migrate the complete quiescent fixture inventory, preserving historical state.",
    }
    return database, packet, settings.target("development")


def historical_state_variants(project):
    """Bind frozen real-owner snapshots to three independent test-owned identities."""
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"
    sources = json.loads((fixtures / "historical-owner-sources.json").read_text())
    workflow = json.loads(sources["unstarted"]["task"][0][7])["workflow"]
    database, packet, path = historical_project(project, workflow_source=workflow, create_backup=False)
    mapped = []
    with sqlite3.connect(database) as connection:
        # Disposable arrangement only; the application under test never gets a SQL editor.
        for table in ("task_events", "submissions", "evidence", "task_execution", "task_workflows",
                      "task_proofs", "content_contracts", "task_methods", "tasks"):
            column = "id" if table == "tasks" else "task_id"
            connection.execute(f"DELETE FROM {table} WHERE {column} IN (?,?)", ("T1", "T2"))
        for state, task_id in (("unstarted", "T1"), ("started", "T2"), ("control", "CONTROL")):
            source = sources[state]
            old = source["task"][0]
            metadata = json.loads(old[7])
            metadata["contract"]["id"] = task_id
            metadata["requirements_snapshot"] = deepcopy(project["task"]["requirements_snapshot"])
            metadata["requirements_agreement"] = deepcopy(project["task"]["requirements_agreement"])
            connection.execute(
                "INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (task_id, *old[1:6], None, json.dumps(metadata)),
            )
            submission_ids = {}
            for sequence, _, stage, iteration, digest, raw in source["submissions"]:
                submission_ids[sequence] = connection.execute(
                    "INSERT INTO submissions(task_id,stage,iteration,digest,data) VALUES(?,?,?,?,?)",
                    (task_id, stage, iteration, digest, raw),
                ).lastrowid
            connection.execute("UPDATE tasks SET current_submission_id=? WHERE id=?",
                               (submission_ids.get(old[6]), task_id))
            event_ids = {}
            for sequence, _, version, at, raw in source["events"]:
                event = json.loads(raw)
                if event.get("submission") is not None:
                    event["submission"] = submission_ids[event["submission"]]
                if "previous" in event and event["previous"]["current_submission_id"] is not None:
                    event["previous"]["current_submission_id"] = submission_ids[event["previous"]["current_submission_id"]]
                event_ids[sequence] = connection.execute(
                    "INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)",
                    (task_id, version, at, json.dumps(event)),
                ).lastrowid
            for _, raw, version in source["execution"]:
                execution = json.loads(raw)
                if execution.get("last_report") is not None:
                    execution["last_report"]["task"] = task_id
                connection.execute("INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
                                   (task_id, json.dumps(execution), version))
            for table, source_name in (("task_workflows", "workflows"), ("task_proofs", "proofs")):
                for _, raw in source[source_name]:
                    connection.execute(f"INSERT INTO {table}(task_id,data) VALUES(?,?)", (task_id, raw))
            for _, version, raw in source["content"]:
                connection.execute("INSERT INTO content_contracts(task_id,version,data) VALUES(?,?,?)", (task_id, version, raw))
            if state == "control":
                continue
            stages = deepcopy(packet["mapping"][0]["stages"])
            for stage in stages:
                stage["criteria"] = {"requirements": [], "definition_of_ready": [], "definition_of_done": []}
                stage["execution"] = {
                    "state": "unknown", "source_revision": None,
                    "source_refs": [{"kind": "contract", "digest": identity(metadata["contract"])}],
                }
                if stage["stage_id"] == "tests":
                    stage["criteria_authority"]["propose"] = ["implementation"]
                if stage["stage_id"] == "implementation":
                    stage["execution"] = {
                        "state": state, "source_revision": None,
                        "source_refs": [{"kind": "task_event", "id": event_ids[2]}] if state == "unstarted" else
                                       [{"kind": "submission", "id": submission_ids[3]}],
                    }
            mapped.append({"kind": "executable_task", "task_id": task_id, "expected_version": old[5],
                           "expected_workflow_digest": identity(workflow), "stages": stages})
    packet["mapping"] = [packet["mapping"][0], *mapped]
    # The process and both active Task mappings share the same explicit authority.
    packet["mapping"][0]["stages"][0]["criteria_authority"]["propose"] = ["implementation"]
    packet["backup_name"] = backup_commands(project).create()["name"]
    return database, packet, path


def publication_rows(project):
    settings = json.loads((project["root"] / "criteria-editor.json").read_text())
    with sqlite3.connect(f"file:{project['root'] / settings['database']}?mode=ro", uri=True) as connection:
        return {
            "heads": connection.execute("SELECT goal_type,revision FROM config_heads ORDER BY goal_type").fetchall(),
            "operations": connection.execute(
                "SELECT request_id,goal_type,source_revision,candidate,receipt,phase FROM config_operations ORDER BY request_id"
            ).fetchall(),
        }


def assert_publication(project, resource, path):
    observed = publication_rows(project)
    assert (resource["goal_type"], resource["new_revision"]) in observed["heads"]
    operations = [row for row in observed["operations"] if row[0] != "historical-process-publication"]
    assert len(operations) == 1
    _, goal, old_revision, candidate, raw_receipt, phase = operations[0]
    document = json.loads(path.read_text())
    receipt = json.loads(raw_receipt)
    assert goal == resource["goal_type"]
    assert phase == "completed"
    assert old_revision == resource["old_revision"]
    assert receipt["revision"] == resource["new_revision"] == identity(document)
    assert receipt["previous_revision"] == old_revision
    assert receipt["config_path"] == str(path)
    assert json.loads(candidate) == document
    assert resource["new_digest"] == identity(document)
    return observed


def assert_history_preserved(before, after, *, control_ids=()):
    for table in ("events", "submissions", "evidence", "execution", "methods", "proofs", "content"):
        assert after[table] == before[table]
    assert [row for row in after["journal"] if row[3] in control_ids] == [
        row for row in before["journal"] if row[3] in control_ids
    ]
    assert len(after["workflows"]) == len(before["workflows"])
    for (old_task, old_raw), (new_task, new_raw) in zip(before["workflows"], after["workflows"], strict=True):
        assert new_task == old_task
        if old_task in control_ids:
            assert new_raw == old_raw
            continue
        for key, value in json.loads(old_raw).items():
            assert json.loads(new_raw)[key] == value
    assert len(after["tasks"]) == len(before["tasks"])
    for old, new in zip(before["tasks"], after["tasks"], strict=True):
        if old[0] in control_ids:
            assert new == old
            continue
        assert new[:7] == old[:7]
        old_metadata, new_metadata = json.loads(old[7]), json.loads(new[7])
        for key, value in old_metadata.items():
            if key not in ("workflow", "contract", "newborn"):
                assert new_metadata[key] == value
        stripped_workflow = deepcopy(new_metadata["workflow"])
        for stage in stripped_workflow["stages"]:
            stage.pop("purpose")
            stage.pop("criteria_authority")
        assert stripped_workflow == old_metadata["workflow"]
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


def assert_mapped_documents(database, packet, path):
    """Observe mapped values directly; no production transformer generates expectations."""
    workflow = json.loads(path.read_text())
    expected_workflow = packet["mapping"][0]["stages"]
    assert [(s["id"], s["purpose"], s["criteria_authority"]) for s in workflow["stages"]] == [
        (s["stage_id"], s["purpose"], s["criteria_authority"]) for s in expected_workflow
    ]
    with sqlite3.connect(database) as connection:
        for resource in packet["mapping"][1:]:
            metadata = json.loads(connection.execute("SELECT metadata FROM tasks WHERE id=?", (resource["task_id"],)).fetchone()[0])
            contract = metadata["newborn"]["draft"] if resource["kind"] == "newborn_task" else metadata["contract"]
            assert [(s["id"], s["purpose"], s["criteria_authority"]) for s in metadata["workflow"]["stages"]] == [
                (s["stage_id"], s["purpose"], s["criteria_authority"]) for s in resource["stages"]
            ]
            assert [(s["stage_id"], s["criteria"]) for s in contract["stage_contracts"]] == [
                (s["stage_id"], s["criteria"]) for s in resource["stages"]
            ]


def assert_definition_digests(before, after, original_workflow, path, resources):
    """Correlate complete owner documents, independently of the product serializer.

    Call together with mapped-value and preservation assertions: a digest of an
    incorrectly migrated document is not by itself evidence of correctness.
    """
    old_tasks = {row[0]: json.loads(row[7]) for row in before["tasks"]}
    new_tasks = {row[0]: json.loads(row[7]) for row in after["tasks"]}
    for resource in resources:
        if resource["kind"] == "workflow_definition":
            old, new = original_workflow, json.loads(path.read_text(encoding="utf-8"))
        else:
            old_meta, new_meta = old_tasks[resource["task_id"]], new_tasks[resource["task_id"]]
            key = "draft" if resource["kind"] == "newborn_task" else "contract"
            old_contract = old_meta["newborn"]["draft"] if key == "draft" else old_meta["contract"]
            new_contract = new_meta["newborn"]["draft"] if key == "draft" else new_meta["contract"]
            old = {"workflow": old_meta["workflow"], key: old_contract}
            new = {"workflow": new_meta["workflow"], key: new_contract}
        assert resource["old_digest"] == identity(old)
        assert resource["new_digest"] == identity(new)


def assert_original_stage_digests(tools, original_metadata, receipt):
    from tests.runtime_services.stage_criteria_helpers import show

    contract = (original_metadata["newborn"]["draft"] if "newborn" in original_metadata
                else original_metadata["contract"])
    for stage in contract["stage_contracts"]:
        source = show(tools, stage["stage_id"])["source"]
        assert source == {
            "kind": "historical", "original_digest": identity(stage),
            "migration_receipt": receipt,
        }
