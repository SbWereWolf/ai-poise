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

from poise.application.task_workflow_migration import TaskWorkflowMigrationCommands
from poise.interfaces import task_workflow_migration
from poise.modules.foundation.errors import PoiseError
from conftest import write_json
from conftest import WorkPoise
from batch.helpers import request
from poise.application.work import WorkTools
from tests.runtime_services.stage_criteria_helpers import show
from tests.migrations.stage_criteria_helpers import (
    FixtureClock, assert_definition_digests, assert_history_preserved, assert_mapped_documents,
    assert_original_stage_digests, assert_publication, identity,
    historical_project, historical_state_variants, publication_rows, snapshot,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", fixture("digest-vectors.json"), ids=lambda item: item["name"])
def test_independent_digest_observer_matches_accepted_literal_vectors(vector):
    value = json.loads(vector["canonical"])
    # Reverse insertion order deliberately; arrays retain their literal order.
    value = dict(reversed(list(value.items())))
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert canonical == vector["canonical"]
    assert len(canonical) == vector["chars"]
    assert identity(value) == vector["sha256"]


@pytest.mark.parametrize("value,error", [(float("nan"), ValueError), (float("inf"), ValueError),
                                         (float("-inf"), ValueError), ("\ud800", UnicodeEncodeError)])
def test_independent_digest_observer_rejects_nonfinite_and_unencodable_values(value, error):
    with pytest.raises(error):
        identity({"value": value})


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
    observed = TaskWorkflowMigrationCommands(port).migrate(packet)
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
        TaskWorkflowMigrationCommands(port).migrate(packet)
    assert port.requests == []


def test_cli_partial_publication_returns_exact_recovery_envelope_and_exit_two(monkeypatch, tmp_path):
    expected = fixture("migration-recovery-response.json")
    port = RecordingPort(expected)
    monkeypatch.setattr(task_workflow_migration, "task_workflow_migration_tools", lambda path: port)
    output = io.StringIO()
    code = task_workflow_migration.execute(
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
        [sys.executable, "-B", "-m", "poise", "task-workflow-migrate", "--config", str(project["config_path"])],
        input=json.dumps(packet), text=True, capture_output=True, check=False,
        cwd=root, env=environment,
    )


def test_real_cli_migrates_complete_historical_inventory_and_exact_replay_preserves_data(project):
    database, packet, workflow_path = historical_project(project)
    before = snapshot(database)
    original_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    command = invoke_cli(project, packet)
    assert command.returncode == 0, (command.stdout, command.stderr)
    response = json.loads(command.stdout)
    assert set(response) == {"status", "request_id", "resources", "receipt", "replayed"}
    assert response["status"] == "migrated"
    assert response["request_id"] == packet["request_id"]
    assert response["replayed"] is False
    assert isinstance(response["receipt"], str) and response["receipt"]
    assert [(r["kind"], r.get("task_id", r.get("goal_type"))) for r in response["resources"]] == [
        ("workflow_definition", "development"), ("executable_task", "T1"), ("executable_task", "T2"),
    ]
    for resource, mapping in zip(response["resources"], packet["mapping"], strict=True):
        assert resource["old_digest"] != resource["new_digest"]
        if resource["kind"] == "workflow_definition":
            assert resource["old_revision"] == mapping["expected_revision"]
        else:
            assert resource["version"] == mapping["expected_version"]
    migrated_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert [s["purpose"] for s in migrated_workflow["stages"]] == [
        "design", "observation", "execution", "observation",
    ]
    for stage in migrated_workflow["stages"]:
        assert stage.pop("criteria_authority") == {"propose": [], "decide": []}
        stage.pop("purpose")
    assert migrated_workflow == original_workflow
    after = snapshot(database)
    assert_history_preserved(before, after)
    assert_mapped_documents(database, packet, workflow_path)
    assert_definition_digests(before, after, original_workflow, workflow_path, response["resources"])
    publication = assert_publication(project, response["resources"][0], workflow_path)
    replay = invoke_cli(project, packet)
    assert replay.returncode == 0, (replay.stdout, replay.stderr)
    assert json.loads(replay.stdout) == {**response, "replayed": True}
    assert snapshot(database) == after
    assert publication_rows(project) == publication
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
    assert_original_stage_digests(tools, json.loads(before["tasks"][0][7]), response["receipt"])
    current = show(tools, "implementation")
    assert current["execution"]["state"] == "unknown"
    assert current["execution"]["bound_revision"] is not None
    assert current["source"]["kind"] == "historical"
    assert current["source"]["migration_receipt"] == response["receipt"]
    assert current["proposals"] == []
    assert current["criteria"] == {"requirements": [], "definition_of_ready": [], "definition_of_done": []}


def test_real_migration_preserves_newborn_draft_revision_and_genuine_unstarted_provenance(project):
    database, packet, workflow_path = historical_project(project, include_newborn=True)
    before = snapshot(database)
    original_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
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
    assert_mapped_documents(database, packet, workflow_path)
    assert_definition_digests(before, after, original_workflow, workflow_path, response["resources"])
    assert_publication(project, response["resources"][0], workflow_path)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM task_execution WHERE task_id=?", ("N1",),
        ).fetchone()[0] == 0
        before_acquisition = connection.execute(
            "SELECT status,claimed_by,version,current_submission_id,metadata "
            "FROM tasks WHERE id=?", ("N1",),
        ).fetchone()
        before_events = connection.execute(
            "SELECT seq,task_id,version,at,data FROM task_events WHERE task_id=? ORDER BY seq",
            ("N1",),
        ).fetchall()
    assert before_acquisition[:4] == ("newborn", None, 0, None)
    original = json.loads(next(row[7] for row in before["tasks"] if row[0] == "N1"))
    tools = WorkTools(WorkPoise(project["config_path"], "inspect-migrated-newborn"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": "N1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert context["status"] == "newborn"
    assert context["task"] == "N1"
    assert context["revision"] == 1
    assert context["claimed_by"] == "inspect-migrated-newborn"
    assert context["draft"] == json.loads(before_acquisition[4])["newborn"]["draft"]
    with sqlite3.connect(database) as connection:
        acquired = connection.execute(
            "SELECT status,claimed_by,version,current_submission_id,metadata "
            "FROM tasks WHERE id=?", ("N1",),
        ).fetchone()
        acquired_events = connection.execute(
            "SELECT seq,task_id,version,at,data FROM task_events WHERE task_id=? ORDER BY seq",
            ("N1",),
        ).fetchall()
        assert connection.execute(
            "SELECT count(*) FROM task_execution WHERE task_id=?", ("N1",),
        ).fetchone()[0] == 0
    assert acquired == ("newborn", "inspect-migrated-newborn", 1, None, before_acquisition[4])
    assert acquired_events[:-1] == before_events
    assert len(acquired_events) == len(before_events) + 1
    assert acquired_events[-1][1:3] == ("N1", 1)
    acquisition_event = json.loads(acquired_events[-1][4])
    assert acquisition_event["event"] == "ownership_acquired"
    assert acquisition_event["stage"] == "newborn"
    assert acquisition_event["iteration"] == 1
    assert_original_stage_digests(tools, original, response["receipt"])
    criteria_states = {}
    for stage in packet["mapping"][-1]["stages"]:
        state = show(tools, stage["stage_id"])
        assert state["criteria"] == stage["criteria"]
        assert state["execution"]["state"] == "unstarted"
        assert state["execution"]["bound_revision"] is None
        criteria_states[stage["stage_id"]] = state
    final = tools.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert final["status"] == "newborn"
    assert final["task"] == "N1"
    assert final["claimed_by"] == "inspect-migrated-newborn"
    assert final["revision"] == context["revision"] == 1
    assert final["draft"] == context["draft"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status,claimed_by,version,current_submission_id,metadata "
            "FROM tasks WHERE id=?", ("N1",),
        ).fetchone() == acquired
        assert connection.execute(
            "SELECT seq,task_id,version,at,data FROM task_events WHERE task_id=? ORDER BY seq",
            ("N1",),
        ).fetchall() == acquired_events
        assert connection.execute(
            "SELECT count(*) FROM task_execution WHERE task_id=?", ("N1",),
        ).fetchone()[0] == 0
    for stage_id, state in criteria_states.items():
        assert show(tools, stage_id) == state


@pytest.mark.parametrize("fault", ["missing_resource", "extra_resource", "stale_version", "missing_stage", "false_start"])
def test_real_migration_preflight_rejects_incomplete_or_false_mapping_before_any_write(project, fault):
    database, packet, workflow_path = historical_project(project)
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
    before, workflow_bytes = snapshot(database), workflow_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected"
    assert isinstance(response["reason"], str) and response["reason"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == workflow_bytes


@pytest.mark.parametrize("fault", [
    "absent_binding", "missing_settings", "malformed_settings", "unregistered_goal",
    "different_registered_target", "no_managed_head", "missing_owner_database",
    "stale_managed_revision", "live_head_drift", "pending_publication",
])
def test_configured_publication_owner_must_match_exact_project_target_and_managed_head(project, fault):
    from poise.infrastructure.goal_config import EditorSettings, FileGoalConfigRepository

    database, packet, workflow_path = historical_project(project)
    path = project["root"] / "criteria-editor.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    owner_database = project["root"] / settings["database"]
    if fault == "absent_binding":
        del project["cfg"]["task_planning"]
        write_json(project["config_path"], project["cfg"])
    elif fault == "missing_settings":
        write_json(project["root"] / "criteria-catalogue.json", {
            "schema": "workflow-catalogue-1", "editor": "missing-editor.json",
            "task_templates": {}, "max_items": 1000,
        })
    elif fault == "malformed_settings":
        write_json(path, {"schema": "not-editor-settings"})
    elif fault == "unregistered_goal":
        settings["workflows"] = {"other": "config/workflows/managed-other.json"}
        write_json(path, settings)
    elif fault == "different_registered_target":
        twin = "config/workflows/unmanaged-twin.json"
        write_json(project["root"] / twin, json.loads(workflow_path.read_text(encoding="utf-8")))
        project["cfg"]["workflows"]["development"] = twin
        write_json(project["config_path"], project["cfg"])
        configured = project["root"] / project["cfg"]["workflows"]["development"]
        assert json.loads(configured.read_text(encoding="utf-8")) == json.loads(workflow_path.read_text(encoding="utf-8"))
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
        document = json.loads(workflow_path.read_text(encoding="utf-8"))
        document["stages"][0]["instruction"] = "An external writer changed the source after its managed publication."
        write_json(workflow_path, document)
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
    before, published = snapshot(database), workflow_path.read_bytes()
    configured_path = project["root"] / project["cfg"]["workflows"]["development"]
    configured_bytes = configured_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected"
    assert response["reason"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == published
    assert configured_path.read_bytes() == configured_bytes
    assert owner_rows() == owner_before


def test_real_migration_rechecks_drift_after_preflight_without_overwriting_the_writer(project):
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration

    database, packet, workflow_path = historical_project(project)
    before = snapshot(database)
    workflow_bytes = workflow_path.read_bytes()

    def drift():
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T2",))

    port = SqliteTaskWorkflowMigration(project["config_path"], FixtureClock(), after_preflight=drift)
    with pytest.raises(PoiseError):
        TaskWorkflowMigrationCommands(port).migrate(packet)
    after = snapshot(database)
    expected = deepcopy(before)
    expected["tasks"][-1] = (*before["tasks"][-1][:5], before["tasks"][-1][5] + 1, *before["tasks"][-1][6:])
    assert after == expected
    assert workflow_path.read_bytes() == workflow_bytes


def test_published_workflow_and_rolled_back_tasks_have_truthful_recovery_and_resumable_receipt(project):
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration

    database, packet, workflow_path = historical_project(project)
    before = snapshot(database)
    original_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    calls = []

    def fail_after_update(task_id):
        calls.append(task_id)
        raise OSError("Fixture failure after Task update, before transaction commit.")

    failing = SqliteTaskWorkflowMigration(project["config_path"], FixtureClock(), after_update=fail_after_update)
    response = TaskWorkflowMigrationCommands(failing).migrate(packet)
    assert calls == ["T1"]
    assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
    assert response["status"] == "recovery_required"
    assert response["request_id"] == packet["request_id"]
    assert response["reason"]
    assert [(r["kind"], r.get("goal_type")) for r in response["resources"]] == [
        ("workflow_definition", "development"),
    ]
    partial = snapshot(database)
    for table in ("tasks", "events", "submissions", "evidence", "execution"):
        assert partial[table] == before[table]
    published = workflow_path.read_bytes()
    assert [s["purpose"] for s in json.loads(published)["stages"]] == [
        "design", "observation", "execution", "observation",
    ]
    publication = assert_publication(project, response["resources"][0], workflow_path)
    assert_definition_digests(before, partial, original_workflow, workflow_path, response["resources"])
    completed_calls = []
    recovered = TaskWorkflowMigrationCommands(SqliteTaskWorkflowMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert recovered["status"] == "migrated"
    assert recovered["receipt"] == response["receipt"]
    assert completed_calls == ["T1", "T2"]
    assert recovered["resources"][:1] == response["resources"]
    assert workflow_path.read_bytes() == published
    assert publication_rows(project) == publication
    assert_mapped_documents(database, packet, workflow_path)
    final = snapshot(database)
    assert_history_preserved(before, final)
    assert_definition_digests(before, final, original_workflow, workflow_path, recovered["resources"])
    replay = TaskWorkflowMigrationCommands(SqliteTaskWorkflowMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert replay == {**recovered, "replayed": True}
    assert completed_calls == ["T1", "T2"]
    assert snapshot(database) == final
    assert publication_rows(project) == publication


def test_genuine_unstarted_started_and_unrelated_terminal_sources_preserve_provenance(project):
    database, packet, workflow_path = historical_state_variants(project)
    before = snapshot(database)
    original_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    source_submission = packet["mapping"][2]["stages"][2]["execution"]["source_refs"][0]["id"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT task_id,stage FROM submissions WHERE seq=?", (source_submission,)).fetchone() == ("T2", "implementation")
        assert connection.execute("SELECT count(*) FROM submissions WHERE task_id=?", ("T1",)).fetchone()[0] == 0
    command = invoke_cli(project, packet)
    assert command.returncode == 0, (command.stdout, command.stderr)
    response = json.loads(command.stdout)
    assert [r.get("task_id") for r in response["resources"][1:]] == ["T1", "T2"]
    after = snapshot(database)
    assert_history_preserved(before, after, control_ids=("CONTROL",))
    assert_mapped_documents(database, packet, workflow_path)
    assert_publication(project, response["resources"][0], workflow_path)
    assert_definition_digests(before, after, original_workflow, workflow_path, response["resources"])
    tools = WorkTools(WorkPoise(project["config_path"], "continue-known-start"))
    tools.invoke(request("bootstrap", {"task": {"id": "T2"}, "decision": None, "feedback": None, "rework_stage": None}))
    assert_original_stage_digests(tools, json.loads(next(row[7] for row in before["tasks"] if row[0] == "T2")), response["receipt"])
    protected = show(tools, "implementation")
    assert protected["execution"]["state"] == "started"
    assert protected["execution"]["bound_revision"] == protected["revision"]
    assert protected["source"]["migration_receipt"] == response["receipt"]
    design = tools.invoke(request("bootstrap", {
        "task": None, "decision": "rework", "feedback": "Attempt refinement without a lawful restart.", "rework_stage": "tests",
    }))
    assert design["stage"] == "tests"
    from tests.runtime_services.stage_criteria_helpers import proposal, verify_packet, version
    candidate = proposal(tools, "implementation")
    state = snapshot(database)
    with pytest.raises(PoiseError, match="target_started_requires_restart"):
        tools.invoke(verify_packet(design, {"request_id": "deny-known-start-refinement", "expected_version": version(tools),
                                          "proposals": [candidate], "decisions": []}))
    assert show(tools, "implementation") == protected
    assert snapshot(database)["tasks"] == state["tasks"]
    assert snapshot(database)["submissions"] == state["submissions"]
    # T1 remains genuinely untouched by T2 continuation and denied refinement.
    assert next(row for row in snapshot(database)["tasks"] if row[0] == "T1") == next(row for row in after["tasks"] if row[0] == "T1")
    from tests.runtime_services.stage_criteria_helpers import handoff
    handoff(tools, "release-known-start-continuation")
    untouched = WorkTools(WorkPoise(project["config_path"], "continue-unstarted-source"))
    context = untouched.invoke(request("bootstrap", {"task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
    assert_original_stage_digests(untouched, json.loads(next(row[7] for row in before["tasks"] if row[0] == "T1")), response["receipt"])
    current = show(untouched, "implementation")
    assert current["execution"]["state"] == "unstarted"
    assert current["execution"]["bound_revision"] is None
    assert current["source"]["migration_receipt"] == response["receipt"]
    candidate = proposal(untouched, "implementation")
    assert untouched.invoke(verify_packet(context, {
        "request_id": "refine-genuine-unstarted-source", "expected_version": version(untouched),
        "proposals": [candidate], "decisions": [],
    }))["status"] == "verified"
    assert show(untouched, "implementation")["execution"] == current["execution"]


def test_preservation_observer_rejects_additions_to_excluded_control_workflow(project):
    database, _, _ = historical_state_variants(project)
    before = snapshot(database)
    controls = ("T1", "T2", "CONTROL")
    assert_history_preserved(before, deepcopy(before), control_ids=controls)
    changed = deepcopy(before)
    index = next(index for index, row in enumerate(changed["workflows"]) if row[0] == "CONTROL")
    task_id, raw = changed["workflows"][index]
    workflow = json.loads(raw)
    workflow["unexpected_migrated_criteria"] = {"revision": 99}
    changed["workflows"][index] = (task_id, json.dumps(workflow))
    with pytest.raises(AssertionError):
        assert_history_preserved(before, changed, control_ids=controls)


def test_unknown_historical_execution_continues_but_cannot_be_refined_without_restart(project):
    from tests.runtime_services.stage_criteria_helpers import next_stage, proposal, verify_packet, version

    # A valid frozen owner-produced contract with no checks. Only import
    # provenance is arranged here: it cannot prove whether execution started.
    source = json.loads(fixture("historical-owner-sources.json")["unstarted"]["task"][0][7])
    database, packet, workflow_path = historical_project(
        project, workflow_source=source["workflow"], contract_source=source["contract"],
    )
    for resource in packet["mapping"]:
        resource["stages"][0]["criteria_authority"]["propose"] = ["implementation"]
    command = invoke_cli(project, packet)
    assert command.returncode == 0, (command.stdout, command.stderr)
    tools = WorkTools(WorkPoise(project["config_path"], "unknown-source-continuation"))
    context = tools.invoke(request("bootstrap", {"task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
    retained = show(tools, "implementation")
    assert retained["execution"]["state"] == "unknown"
    assert retained["execution"]["bound_revision"] == retained["revision"]
    assert retained["proposals"] == []
    # Enter and actually verify the protected execution target, not just design.
    assert tools.invoke(verify_packet(context))["status"] == "verified"
    observation = next_stage(tools)
    assert observation["stage"] == "test_review"
    assert tools.invoke(verify_packet(observation))["status"] == "verified"
    execution = next_stage(tools)
    assert execution["stage"] == "implementation"
    assert execution["purpose"] == "execution"
    assert show(tools, "implementation")["execution"] == retained["execution"]
    assert show(tools, "implementation")["source"] == retained["source"]
    executed = tools.invoke(verify_packet(execution))
    assert executed["status"] == "verified"
    assert executed["stage"] == "implementation"
    with sqlite3.connect(database) as connection:
        submission = connection.execute(
            "SELECT stage,iteration FROM submissions WHERE seq=(SELECT current_submission_id FROM tasks WHERE id=?)",
            ("T1",),
        ).fetchone()
    assert submission == ("implementation", execution["iteration"])
    completed = show(tools, "implementation")
    assert completed["execution"] == retained["execution"]
    assert completed["revision"] == retained["revision"]
    assert completed["criteria"] == retained["criteria"]
    assert completed["source"] == retained["source"]
    design = tools.invoke(request("bootstrap", {"task": None, "decision": "rework", "feedback": "Attempt to change a protected historical target.", "rework_stage": "tests"}))
    protected = show(tools, "implementation")
    assert {key: value for key, value in protected.items() if key != "history"} == {
        key: value for key, value in completed.items() if key != "history"
    }
    assert protected["history"][:len(completed["history"])] == completed["history"]
    before, workflow_bytes, owner = snapshot(database), workflow_path.read_bytes(), publication_rows(project)
    candidate = proposal(tools, "implementation")
    with pytest.raises(PoiseError, match="target_started_requires_restart"):
        tools.invoke(verify_packet(design, {"request_id": "deny-unknown-state-refinement", "expected_version": version(tools),
                                          "proposals": [candidate], "decisions": []}))
    assert show(tools, "implementation") == protected
    assert snapshot(database)["tasks"] == before["tasks"]
    assert snapshot(database)["submissions"] == before["submissions"]
    assert workflow_path.read_bytes() == workflow_bytes
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["missing_backup", "corrupt_backup", "backup_mismatch", "foreign_submission", "missing_event", "wrong_contract", "false_revision"])
def test_backup_and_source_reference_failures_reject_without_mapped_writes(project, fault):
    from tests.backups.helpers import backup_directory

    database, packet, workflow_path = historical_project(project)
    backup = backup_directory(project) / packet["backup_name"]
    if fault == "missing_backup":
        packet["backup_name"] = "absent-criteria-source.sqlite"
    elif fault == "corrupt_backup":
        backup.write_bytes(b"not a SQLite backup")
    elif fault == "backup_mismatch":
        with sqlite3.connect(backup) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T2",))
    elif fault == "false_revision":
        packet["mapping"][1]["stages"][2]["execution"]["source_revision"] = 12
    elif fault == "wrong_contract":
        packet["mapping"][1]["stages"][2]["execution"]["source_refs"] = [{"kind": "contract", "digest": "0" * 64}]
    else:
        with sqlite3.connect(database) as connection:
            foreign = connection.execute("SELECT seq FROM submissions WHERE task_id=?", ("T2",)).fetchone()[0]
        packet["mapping"][1]["stages"][2]["execution"]["source_refs"] = [
            {"kind": "submission", "id": foreign} if fault == "foreign_submission" else {"kind": "task_event", "id": 999999},
        ]
    before, published, owner = snapshot(database), workflow_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["missing", "duplicate", "incompatible"])
def test_original_stage_contract_must_exist_uniquely_before_migration_writes(project, fault):
    from tests.backups.helpers import backup_commands

    database, packet, workflow_path = historical_project(project, create_backup=False)
    with sqlite3.connect(database) as connection:
        metadata = json.loads(connection.execute("SELECT metadata FROM tasks WHERE id=?", ("T1",)).fetchone()[0])
        contracts = metadata["contract"]["stage_contracts"]
        if fault == "missing":
            contracts.pop(2)
        elif fault == "duplicate":
            contracts.append(deepcopy(contracts[2]))
        else:
            contracts[2]["allowed_paths"] = None
        connection.execute("UPDATE tasks SET metadata=? WHERE id=?", (json.dumps(metadata), "T1"))
    # A genuine backup of this unsupported source, not a backup mismatch.
    packet["backup_name"] = backup_commands(project).create()["name"]
    for stage in packet["mapping"][1]["stages"]:
        stage["execution"]["source_refs"] = [{"kind": "contract", "digest": identity(metadata["contract"])}]
    before, published, owner = snapshot(database), workflow_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["nonfinite", "unencodable"])
def test_unhashable_original_definition_is_rejected_before_mapped_writes(project, fault):
    from tests.backups.helpers import backup_commands

    database, packet, workflow_path = historical_project(project, create_backup=False)
    with sqlite3.connect(database) as connection:
        metadata = json.loads(connection.execute("SELECT metadata FROM tasks WHERE id=?", ("T1",)).fetchone()[0])
        # The complete historical contract is the preimage; this is source
        # corruption, not an unknown request option or a stale digest.
        metadata["contract"]["requirements"] = [float("nan") if fault == "nonfinite" else "\ud800"]
        connection.execute("UPDATE tasks SET metadata=? WHERE id=?", (json.dumps(metadata), "T1"))
        source_event = connection.execute("SELECT seq FROM task_events WHERE task_id=?", ("T1",)).fetchone()[0]
    packet["backup_name"] = backup_commands(project).create()["name"]
    for stage in packet["mapping"][1]["stages"]:
        stage["execution"]["source_refs"] = [{"kind": "task_event", "id": source_event}]
    before, published, owner = snapshot(database), workflow_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("drift", ["published_process", "remaining_task"])
def test_partial_publication_recovery_refuses_intervening_drift_without_overwrite(project, drift):
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration

    database, packet, workflow_path = historical_project(project)

    def failure(_):
        raise OSError("Stop after process publication with the Task transaction uncommitted.")

    partial = TaskWorkflowMigrationCommands(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock(), after_update=failure)).migrate(packet)
    assert partial["status"] == "recovery_required"
    owner = assert_publication(project, partial["resources"][0], workflow_path)
    if drift == "published_process":
        document = json.loads(workflow_path.read_text())
        document["stages"][0]["instruction"] = "An intervening writer owns this changed instruction."
        write_json(workflow_path, document)
    else:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T2",))
    before, published = snapshot(database), workflow_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
    assert response["status"] == "recovery_required" and response["reason"]
    assert response["request_id"] == packet["request_id"]
    assert response["receipt"] == partial["receipt"]
    assert response["resources"] == partial["resources"]
    assert snapshot(database) == before
    assert workflow_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("schema,task_ids", [
    ("task-workflow-migration-1", ["0077", "0081", "0079", "0078", "0074", "0063"]),
    ("task-workflow-migration-2", ["0082"]),
    ("task-workflow-migration-3", ["0082"]),
    ("task-workflow-migration-4", ["ERP-HARNESS-COMPARISON-PLAN"]),
])
def test_historical_migration_schemas_keep_their_exact_request_contract(schema, task_ids):
    from poise.modules.tasks.workflow_migration import MigrationRequest

    packet = {"schema": schema, "request_id": "preserve-historical-wire", "backup_name": "fixture.sqlite",
              "task_ids": task_ids, "authorization": "Preserve the existing schema contract."}
    observed = MigrationRequest.parse(packet)
    assert observed.schema == schema
    assert list(observed.task_ids) == task_ids
    changed = {**packet, "mapping": []}
    with pytest.raises(PoiseError):
        MigrationRequest.parse(changed)


@pytest.mark.parametrize("case", [
    "completed_same_intent", "absent_completed", "foreign_completion", "workflow_only",
    "conflicting_completions", "candidate_drift", "pending",
])
def test_migrated_available_task_requires_completed_same_intent_evidence(project, case):
    from poise.composition import project_tools
    from tests.projects.helpers import setup_case

    def owner_records():
        with sqlite3.connect(project["root"] / fixture("migration-editor.json")["database"]) as connection:
            digests = connection.execute(
                "SELECT request_id,request_digest FROM config_operations ORDER BY request_id",
            ).fetchall()
        return publication_rows(project), digests

    database, packet, workflow_path = historical_project(project)
    original = snapshot(database)
    migrated = invoke_cli(project, packet)
    assert migrated.returncode == 0, (migrated.stdout, migrated.stderr)
    receipt = json.loads(migrated.stdout)
    settings, _, _ = setup_case(project)
    write_json(project["root"] / "state/project-registry.json", fixture("boundary-project-registry.json"))
    with sqlite3.connect(database) as connection:
        records = connection.execute(
            "SELECT seq,at,session_id,task_id,data FROM journal WHERE event=? ORDER BY seq",
            ("task_stage_criteria_migration",),
        ).fetchall()
        completed = [(row, json.loads(row[4])) for row in records if json.loads(row[4])["phase"] == "completed"]
        assert len(completed) == 1
        row, completion = completed[0]
        assert completion["result"] == receipt
        if case == "absent_completed":
            connection.execute("DELETE FROM journal WHERE seq=?", (row[0],))
        elif case in ("foreign_completion", "workflow_only", "conflicting_completions"):
            changed = deepcopy(completion)
            if case == "foreign_completion":
                changed["result"]["receipt"] = "f" * 64
                changed["result"]["resources"][1]["task_id"] = "FOREIGN"
            elif case == "workflow_only":
                changed["result"]["resources"] = changed["result"]["resources"][:1]
            else:
                # Both records retain the same request/receipt/Task identity;
                # their claimed candidate differs, rather than being foreign.
                changed["result"]["resources"][1]["new_digest"] = "e" * 64
            if case == "conflicting_completions":
                connection.execute(
                    "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
                    (row[1], row[2], row[3], "task_stage_criteria_migration", json.dumps(changed)),
                )
            else:
                connection.execute("UPDATE journal SET data=? WHERE seq=?", (json.dumps(changed), row[0]))
        elif case == "candidate_drift":
            metadata = json.loads(connection.execute("SELECT metadata FROM tasks WHERE id=?", ("T1",)).fetchone()[0])
            metadata["stage_criteria"]["stages"]["implementation"]["criteria"]["definition_of_done"] = [
                "An intervening writer specifies another valid criterion.",
            ]
            connection.execute("UPDATE tasks SET metadata=? WHERE id=?", (json.dumps(metadata), "T1"))
        elif case == "pending":
            execution = json.loads(connection.execute("SELECT data FROM task_execution WHERE task_id=?", ("T1",)).fetchone()[0])
            execution["pending"] = "checks"
            connection.execute("UPDATE task_execution SET data=? WHERE task_id=?", (json.dumps(execution), "T1"))

    tools = WorkTools(WorkPoise(project["config_path"], "completed-migration-start"))
    before, owner, published = snapshot(database), owner_records(), workflow_path.read_bytes()
    files = {path: path.read_bytes() for path in (
        project["config_path"], settings, project["root"] / "criteria-editor.json",
        project["root"] / "criteria-catalogue.json", project["root"] / "state/project-registry.json",
    )}
    listed = project_tools(settings).next("demo")
    assert snapshot(database) == before
    assert owner_records() == owner
    assert workflow_path.read_bytes() == published
    assert all(path.read_bytes() == content for path, content in files.items())
    identities = [(item["project"], item["task"]) for item in listed["tasks"]]
    bootstrap = request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    })
    if case != "completed_same_intent":
        assert ("demo", "T1") not in identities
        with pytest.raises(PoiseError):
            tools.invoke(bootstrap)
        after = snapshot(database)
        for table in before:
            if table != "journal":
                assert after[table] == before[table], table
        # Runtime/refusal audit may append; the migration journal cannot change.
        assert [row for row in after["journal"] if row[4] == "task_stage_criteria_migration"] == [
            row for row in before["journal"] if row[4] == "task_stage_criteria_migration"
        ]
    else:
        # Invoke even when discovery wrongly excluded T1, to expose the actual
        # retained-execution admission failure rather than only an empty list.
        context = tools.invoke(bootstrap)
        assert listed["status"] == "listed" and listed["errors"] == []
        assert identities.count(("demo", "T1")) == 1
        assert len(identities) == len(set(identities))
        assert context["task"] == "T1" and context["status"] == "active"
        assert_original_stage_digests(tools, json.loads(original["tasks"][0][7]), receipt["receipt"])
        criterion = show(tools, "implementation")
        assert criterion["execution"]["state"] == "unknown"
        assert criterion["execution"]["bound_revision"] is not None
        assert criterion["source"]["kind"] == "historical"
        assert criterion["source"]["migration_receipt"] == receipt["receipt"]
        assert criterion["proposals"] == []
        after = snapshot(database)
        assert after["tasks"][1] == before["tasks"][1]
        for table in ("submissions", "evidence", "proofs", "content", "methods"):
            assert after[table] == before[table], table
        metadata = json.loads(after["tasks"][0][7])
        assert metadata.get("restart_history", []) == []
        assert after["tasks"][0][4] == "completed-migration-start"
        assert context["worktree"] and Path(context["worktree"]).is_dir()
    assert owner_records() == owner
    assert workflow_path.read_bytes() == published
    assert all(path.read_bytes() == content for path, content in files.items())


def test_migrated_available_task_rechecks_current_claim_after_discovery(project):
    from poise.composition import project_tools
    from tests.projects.helpers import setup_case

    database, packet, workflow_path = historical_project(project)
    migrated = invoke_cli(project, packet)
    assert migrated.returncode == 0, (migrated.stdout, migrated.stderr)
    settings, _, _ = setup_case(project)
    write_json(project["root"] / "state/project-registry.json", fixture("boundary-project-registry.json"))
    tools = WorkTools(WorkPoise(project["config_path"], "post-discovery-start"))
    before = snapshot(database)
    listed = project_tools(settings).next("demo")
    assert snapshot(database) == before
    assert ("demo", "T1") in [(item["project"], item["task"]) for item in listed["tasks"]]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "UPDATE tasks SET claimed_by=?,version=version+1 WHERE id=? AND version=? AND claimed_by IS NULL",
            ("foreign-owner-after-discovery", "T1", 7),
        ).rowcount == 1
    writer = snapshot(database)
    owner, published = publication_rows(project), workflow_path.read_bytes()
    with pytest.raises(PoiseError):
        tools.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
        }))
    after = snapshot(database)
    for table in writer:
        if table != "journal":
            assert after[table] == writer[table], table
    assert after["tasks"][0][4:6] == ("foreign-owner-after-discovery", 8)
    assert publication_rows(project) == owner
    assert workflow_path.read_bytes() == published


@pytest.mark.parametrize("case", ["preintent_manifest", "resume_target_binding", "final_manifest"])
def test_migration_manifest_drift_preserves_writer_and_distinguishes_preintent_recovery(project, monkeypatch, case):
    from poise.infrastructure.goal_config import FileGoalConfigRepository
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration

    database, packet, workflow_path = historical_project(project)
    original_tasks = snapshot(database)
    original_manifest = project["config_path"].read_bytes()
    original_workflow = workflow_path.read_bytes()
    owner_before = publication_rows(project)

    def operation_records():
        with sqlite3.connect(project["root"] / fixture("migration-editor.json")["database"]) as connection:
            return connection.execute(
                "SELECT request_id,request_digest,goal_type,source_revision,candidate,receipt,phase "
                "FROM config_operations ORDER BY request_id",
            ).fetchall()

    operations_before = operation_records()
    observed = []
    writer = {}
    original_publish = FileGoalConfigRepository.publish_migration

    def change_manifest():
        manifest = json.loads(original_manifest)
        # A valid operational change, not malformed JSON or a reader mock.
        manifest["limits"]["git_seconds"] = 16.0
        write_json(project["config_path"], manifest)
        writer[project["config_path"]] = project["config_path"].read_bytes()
        observed.append("manifest_writer")

    def fail_update(task_id):
        observed.append(("after_update", task_id))
        raise OSError("A real process publication precedes this fixture Task rollback.")

    def publish_then_write(self, *args, **kwargs):
        result = original_publish(self, *args, **kwargs)
        if not writer:
            # The real owner has released its lock and committed its operation.
            assert_publication(project, {
                "goal_type": "development", "old_revision": result["previous_revision"],
                "new_revision": result["revision"], "new_digest": identity(json.loads(workflow_path.read_bytes())),
            }, workflow_path)
            observed.append("owner_completed")
            change_manifest()
        return result

    def execute(port):
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(task_workflow_migration, "task_workflow_migration_tools", lambda path: TaskWorkflowMigrationCommands(port))
            code = task_workflow_migration.execute(
                project["config_path"], io.BytesIO(json.dumps(packet).encode()), output,
            )
        return code, json.loads(output.getvalue())

    if case == "resume_target_binding":
        code, partial = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock(), after_update=fail_update))
        assert code == 2 and partial["status"] == "recovery_required"
        assert observed == [("after_update", "T1")]
        assert len(partial["resources"]) == 1
        assert_publication(project, partial["resources"][0], workflow_path)
        alternate = project["root"] / "config/workflows/writer-development.json"
        write_json(alternate, json.loads(workflow_path.read_bytes()))
        alternate_editor = project["root"] / "writer-editor.json"
        editor = fixture("migration-editor.json")
        editor["workflows"]["development"] = "config/workflows/writer-development.json"
        write_json(alternate_editor, editor)
        alternate_catalogue = project["root"] / "writer-catalogue.json"
        catalogue = fixture("migration-catalogue.json")
        catalogue["editor"] = "writer-editor.json"
        write_json(alternate_catalogue, catalogue)
        manifest = json.loads(original_manifest)
        manifest["workflows"]["development"] = "config/workflows/writer-development.json"
        manifest["task_planning"]["catalogue"] = str(alternate_catalogue)
        write_json(project["config_path"], manifest)
        writer = {path: path.read_bytes() for path in (
            alternate, alternate_editor, alternate_catalogue, project["config_path"],
        )}
        before = snapshot(database)
        owner_before = publication_rows(project)
        code, response = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock()))
    elif case == "preintent_manifest":
        before = snapshot(database)
        code, response = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock(), after_preflight=change_manifest))
    else:
        before = snapshot(database)
        with monkeypatch.context() as patch:
            patch.setattr(FileGoalConfigRepository, "publish_migration", publish_then_write)
            code, response = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock()))

    assert writer and all(path.read_bytes() == content for path, content in writer.items())
    if case == "preintent_manifest":
        assert observed == ["manifest_writer"]
        assert code == 2, response
        assert set(response) == {"status", "reason"}
        assert response["status"] == "rejected" and response["reason"]
        assert snapshot(database) == before
        assert publication_rows(project) == owner_before
        assert operation_records() == operations_before
        assert workflow_path.read_bytes() == original_workflow
    else:
        if case == "final_manifest":
            assert observed == ["owner_completed", "manifest_writer"]
        assert code == 2, response
        assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
        assert response["status"] == "recovery_required" and response["reason"]
        assert response["request_id"] == packet["request_id"]
        assert isinstance(response["receipt"], str) and response["receipt"]
        assert len(response["resources"]) == 1
        if case == "resume_target_binding":
            assert response["receipt"] == partial["receipt"]
            assert response["resources"] == partial["resources"]
        owner = assert_publication(project, response["resources"][0], workflow_path)
        operations = operation_records()
        after = snapshot(database)
        for table in original_tasks:
            if table != "journal":
                assert after[table] == original_tasks[table], table
        assert not any(json.loads(row[5]).get("phase") == "completed"
                       for row in after["journal"] if row[4] == "task_stage_criteria_migration")
        published = workflow_path.read_bytes()
        retry_code, retry = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock()))
        assert retry_code == 2 and retry["status"] == "recovery_required"
        assert retry["receipt"] == response["receipt"] and retry["resources"] == response["resources"]
        assert snapshot(database) == after
        assert publication_rows(project) == owner
        assert operation_records() == operations
        assert workflow_path.read_bytes() == published
        assert all(path.read_bytes() == content for path, content in writer.items())
        # Explicit fixture writer restores its own manifest, never source state.
        write_json(project["config_path"], json.loads(original_manifest))
        recovered_code, recovered = execute(SqliteTaskWorkflowMigration(project["config_path"], FixtureClock()))
        assert recovered_code == 0 and recovered["status"] == "migrated"
        assert recovered["receipt"] == response["receipt"]
        assert recovered["resources"][:1] == response["resources"]
        assert publication_rows(project) == owner
        assert operation_records() == operations
        assert workflow_path.read_bytes() == published
        for path, content in writer.items():
            if path != project["config_path"]:
                assert path.read_bytes() == content


def test_migration_reports_owner_completion_before_coordinator_observation_and_replays_once(project, monkeypatch):
    from poise.infrastructure.goal_config import FileGoalConfigRepository
    from poise.infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration

    database, packet, workflow_path = historical_project(project)
    before = snapshot(database)
    old_document = json.loads(workflow_path.read_bytes())
    observed = []
    original_publish = FileGoalConfigRepository.publish_migration

    def migration_operations():
        with sqlite3.connect(project["root"] / fixture("migration-editor.json")["database"]) as connection:
            return connection.execute(
                "SELECT request_id,request_digest,goal_type,source_revision,candidate,receipt,phase "
                "FROM config_operations WHERE request_id<>? ORDER BY request_id",
                ("historical-process-publication",),
            ).fetchall()

    def completed_then_fail(self, *args, **kwargs):
        result = original_publish(self, *args, **kwargs)
        if not observed:
            document = json.loads(workflow_path.read_bytes())
            # Independent summary preimages use the actual preserved old file
            # and literal expected new purpose/grants, not product builders.
            expected_document = fixture("boundary-migrated-workflow.json")
            assert document == expected_document
            summary = {
                "kind": "workflow_definition", "goal_type": "development",
                "old_digest": identity(old_document), "new_digest": identity(expected_document),
                "old_revision": packet["mapping"][0]["expected_revision"],
                "new_revision": identity(expected_document),
            }
            owner = assert_publication(project, summary, workflow_path)
            rows = migration_operations()
            assert len(rows) == 1 and rows[0][6] == "completed"
            assert json.loads(rows[0][5]) == result
            observed.append((summary, owner, rows, workflow_path.read_bytes()))
            raise OSError("Editor effect is durable before coordinator observation.")
        return result

    output = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(FileGoalConfigRepository, "publish_migration", completed_then_fail)
        patch.setattr(task_workflow_migration, "task_workflow_migration_tools", lambda path:
                      TaskWorkflowMigrationCommands(SqliteTaskWorkflowMigration(path, FixtureClock())))
        code = task_workflow_migration.execute(
            project["config_path"], io.BytesIO(json.dumps(packet).encode()), output,
        )
    assert len(observed) == 1
    expected_summary, owner, operations, published = observed[0]
    partial = snapshot(database)
    for table in before:
        if table != "journal":
            assert partial[table] == before[table], table
    phases = [json.loads(row[5])["phase"] for row in partial["journal"]
              if row[4] == "task_stage_criteria_migration"]
    assert phases == ["intent"]
    response = json.loads(output.getvalue())
    assert code == 2 and response["status"] == "recovery_required"
    assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
    assert response["request_id"] == packet["request_id"] and response["receipt"]
    assert response["reason"] == "Editor effect is durable before coordinator observation."
    assert response["resources"] == [expected_summary]
    recovered = invoke_cli(project, packet)
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    success = json.loads(recovered.stdout)
    assert success["status"] == "migrated" and success["replayed"] is False
    assert success["receipt"] == response["receipt"]
    assert success["resources"][:1] == [expected_summary]
    assert publication_rows(project) == owner
    assert migration_operations() == operations
    assert workflow_path.read_bytes() == published
    after = snapshot(database)
    completed = [json.loads(row[5]) for row in after["journal"]
                 if row[4] == "task_stage_criteria_migration" and json.loads(row[5])["phase"] == "completed"]
    assert len(completed) == 1 and completed[0]["result"] == success
    replay = invoke_cli(project, packet)
    assert replay.returncode == 0, (replay.stdout, replay.stderr)
    assert json.loads(replay.stdout) == {**success, "replayed": True}
    assert snapshot(database) == after
    assert publication_rows(project) == owner
    assert migration_operations() == operations
    assert workflow_path.read_bytes() == published


@pytest.fixture
def opaque_execution_db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE task_execution("
        "task_id TEXT PRIMARY KEY,data TEXT NOT NULL,version INTEGER NOT NULL)"
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("original_value,attempted_value", [
    pytest.param(1, True, id="scalar-number-to-boolean"),
    pytest.param({"items": [1]}, {"items": [True]}, id="nested-number-to-boolean"),
    pytest.param(1, 2, id="changed-number-control"),
])
def test_execution_owner_rejects_changed_opaque_json_without_writing(
    opaque_execution_db, original_value, attempted_value,
):
    from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository

    original = fixture("historical-state.json")["execution"]
    original["opaque_review_value"] = deepcopy(original_value)
    raw = json.dumps(original, ensure_ascii=False, indent=2)
    opaque_execution_db.execute(
        "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
        ("T-opaque", raw, 3),
    )
    before = tuple(opaque_execution_db.execute(
        "SELECT data,version FROM task_execution WHERE task_id=?", ("T-opaque",),
    ).fetchone())
    assert before == (raw, 3)
    candidate = {**deepcopy(original), "opaque_review_value": deepcopy(attempted_value)}

    with pytest.raises(PoiseError) as failure:
        SqliteExecutionRepository(opaque_execution_db).save("T-opaque", candidate, 3)

    assert str(failure.value) == "Из execution adapter нельзя менять Task lifecycle"
    assert tuple(opaque_execution_db.execute(
        "SELECT data,version FROM task_execution WHERE task_id=?", ("T-opaque",),
    ).fetchone()) == before


@pytest.mark.parametrize("original_value", [
    pytest.param(1, id="scalar-number"),
    pytest.param({"items": [1]}, id="nested-number"),
])
@pytest.mark.parametrize("omit_opaque", [False, True], ids=["explicit-opaque", "omitted-opaque"])
def test_execution_owner_updates_current_fields_and_preserves_opaque_json_types(
    opaque_execution_db, original_value, omit_opaque,
):
    from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository

    original = fixture("historical-state.json")["execution"]
    original["opaque_review_value"] = deepcopy(original_value)
    opaque_execution_db.execute(
        "INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)",
        ("T-opaque", json.dumps(original, ensure_ascii=False, indent=2), 3),
    )
    candidate = {**deepcopy(original), "attempts": 1}
    if omit_opaque:
        del candidate["historical_marker"]
        del candidate["opaque_review_value"]

    returned = SqliteExecutionRepository(opaque_execution_db).save("T-opaque", candidate, 3)

    raw, version = opaque_execution_db.execute(
        "SELECT data,version FROM task_execution WHERE task_id=?", ("T-opaque",),
    ).fetchone()
    actual = json.loads(raw)
    expected = {**deepcopy(original), "attempts": 1}
    assert returned == version == 4
    assert actual == expected
    assert type(actual["attempts"]) is int
    assert actual["historical_marker"] == "preserve"
    if type(original_value) is int:
        assert actual["opaque_review_value"] == 1
        assert type(actual["opaque_review_value"]) is int
    else:
        assert actual["opaque_review_value"] == {"items": [1]}
        assert type(actual["opaque_review_value"]["items"][0]) is int
