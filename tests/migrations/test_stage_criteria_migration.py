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
    assert_mapped_documents(database, packet, process_path)
    assert_definition_digests(before, after, original_process, process_path, response["resources"])
    publication = assert_publication(project, response["resources"][0], process_path)
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
    database, packet, process_path = historical_project(project, include_newborn=True)
    before = snapshot(database)
    original_process = json.loads(process_path.read_text(encoding="utf-8"))
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
    assert_mapped_documents(database, packet, process_path)
    assert_definition_digests(before, after, original_process, process_path, response["resources"])
    assert_publication(project, response["resources"][0], process_path)
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
    original_process = json.loads(process_path.read_text(encoding="utf-8"))
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
    publication = assert_publication(project, response["resources"][0], process_path)
    assert_definition_digests(before, partial, original_process, process_path, response["resources"])
    completed_calls = []
    recovered = TaskProcessMigrationCommands(SqliteTaskProcessMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert recovered["status"] == "migrated"
    assert recovered["receipt"] == response["receipt"]
    assert completed_calls == ["T1", "T2"]
    assert recovered["resources"][:1] == response["resources"]
    assert process_path.read_bytes() == published
    assert publication_rows(project) == publication
    assert_mapped_documents(database, packet, process_path)
    final = snapshot(database)
    assert_history_preserved(before, final)
    assert_definition_digests(before, final, original_process, process_path, recovered["resources"])
    replay = TaskProcessMigrationCommands(SqliteTaskProcessMigration(
        project["config_path"], FixtureClock(), after_update=completed_calls.append,
    )).migrate(packet)
    assert replay == {**recovered, "replayed": True}
    assert completed_calls == ["T1", "T2"]
    assert snapshot(database) == final
    assert publication_rows(project) == publication


def test_genuine_unstarted_started_and_unrelated_terminal_sources_preserve_provenance(project):
    database, packet, process_path = historical_state_variants(project)
    before = snapshot(database)
    original_process = json.loads(process_path.read_text(encoding="utf-8"))
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
    assert_mapped_documents(database, packet, process_path)
    assert_publication(project, response["resources"][0], process_path)
    assert_definition_digests(before, after, original_process, process_path, response["resources"])
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
    database, packet, process_path = historical_project(
        project, process_source=source["process"], contract_source=source["contract"],
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
    before, process_bytes, owner = snapshot(database), process_path.read_bytes(), publication_rows(project)
    candidate = proposal(tools, "implementation")
    with pytest.raises(PoiseError, match="target_started_requires_restart"):
        tools.invoke(verify_packet(design, {"request_id": "deny-unknown-state-refinement", "expected_version": version(tools),
                                          "proposals": [candidate], "decisions": []}))
    assert show(tools, "implementation") == protected
    assert snapshot(database)["tasks"] == before["tasks"]
    assert snapshot(database)["submissions"] == before["submissions"]
    assert process_path.read_bytes() == process_bytes
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["missing_backup", "corrupt_backup", "backup_mismatch", "foreign_submission", "missing_event", "wrong_contract", "false_revision"])
def test_backup_and_source_reference_failures_reject_without_mapped_writes(project, fault):
    from tests.backups.helpers import backup_directory

    database, packet, process_path = historical_project(project)
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
    before, published, owner = snapshot(database), process_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["missing", "duplicate", "incompatible"])
def test_original_stage_contract_must_exist_uniquely_before_migration_writes(project, fault):
    from tests.backups.helpers import backup_commands

    database, packet, process_path = historical_project(project, create_backup=False)
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
    before, published, owner = snapshot(database), process_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("fault", ["nonfinite", "unencodable"])
def test_unhashable_original_definition_is_rejected_before_mapped_writes(project, fault):
    from tests.backups.helpers import backup_commands

    database, packet, process_path = historical_project(project, create_backup=False)
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
    before, published, owner = snapshot(database), process_path.read_bytes(), publication_rows(project)
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "reason"}
    assert response["status"] == "rejected" and response["reason"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == published
    assert publication_rows(project) == owner


@pytest.mark.parametrize("drift", ["published_process", "remaining_task"])
def test_partial_publication_recovery_refuses_intervening_drift_without_overwrite(project, drift):
    from poise.infrastructure.task_process_migration import SqliteTaskProcessMigration

    database, packet, process_path = historical_project(project)

    def failure(_):
        raise OSError("Stop after process publication with the Task transaction uncommitted.")

    partial = TaskProcessMigrationCommands(SqliteTaskProcessMigration(project["config_path"], FixtureClock(), after_update=failure)).migrate(packet)
    assert partial["status"] == "recovery_required"
    owner = assert_publication(project, partial["resources"][0], process_path)
    if drift == "published_process":
        document = json.loads(process_path.read_text())
        document["stages"][0]["instruction"] = "An intervening writer owns this changed instruction."
        write_json(process_path, document)
    else:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T2",))
    before, published = snapshot(database), process_path.read_bytes()
    command = invoke_cli(project, packet)
    assert command.returncode == 2
    response = json.loads(command.stdout)
    assert set(response) == {"status", "request_id", "receipt", "resources", "reason"}
    assert response["status"] == "recovery_required" and response["reason"]
    assert response["request_id"] == packet["request_id"]
    assert response["receipt"] == partial["receipt"]
    assert response["resources"] == partial["resources"]
    assert snapshot(database) == before
    assert process_path.read_bytes() == published
    assert publication_rows(project) == owner


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
