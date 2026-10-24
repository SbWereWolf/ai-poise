"""A02: real general profiles, optional Task composition and publication.

All arrangements occur inside CALL. A current publication refusal is explicit
missing-profile admission RED; it never claims downstream execution happened.
Isolated entry instrumentation observes Task imports/construction/queries.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from poise.common import PoiseError
from tests.hook_transport.architecture_support import GeneralScene, literal, write


def adapted(scene):
    return {**scene.project, "cfg": scene.profile, "config_path": scene.path}


def test_common_profile_admits_without_task_fields(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_PROFILE"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    clean = scene.invoke("admit")
    assert clean["project"] == project["cfg"]["project"], marker
    assert clean["checkout"] == str(project["app"]), marker
    assert not {"stages", "checks", "accounting", "source_reader", "task_ids"} & scene.profile.keys(), marker
    options = literal("unknown-config-options.json")
    scene.profile.update(options["root"])
    scene.profile["limits"].update(options["nested"]["limits"])
    scene.publish(request_id="unknown-options")
    changed = scene.invoke("admit")
    assert changed["project"] == clean["project"] and changed["checkout"] == clean["checkout"], marker
    assert json.loads(scene.path.read_text()) == scene.profile, marker
    from poise.infrastructure.project_binding import read_project_checkout
    other = tmp_path / "foreign-checkout"
    other.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(other)], capture_output=True, check=True)
    with pytest.raises(PoiseError):
        read_project_checkout(scene.path, other)
    for problem in ("missing-known", "wrong-type", "schema"):
        candidate = deepcopy(scene.profile)
        if problem == "missing-known":
            candidate.pop("limits")
        elif problem == "wrong-type":
            candidate["limits"]["output_chars"] = "five thousand"
        else:
            candidate["schema"] = "poise-harness-project-999"
        before = scene.path.read_bytes()
        with pytest.raises(PoiseError):
            scene.owner.apply(scene.transition_packet(problem, candidate))
        assert scene.path.read_bytes() == before, marker


def test_general_session_without_task_owner(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_SESSION"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    generated = scene.invoke("session")
    again = scene.invoke("session")
    assert generated["first"] == generated["second"] == again["first"], marker
    assert generated["origin"] == "generated", marker
    native_env = {k: v for k, v in scene.env.items() if k != "POISE_CALLER_BINDING"}
    native_env["CODEX_THREAD_ID"] = "architecture-native"
    native = scene.invoke("session", environment=native_env)
    assert native["first"] == native["second"] == "architecture-native", marker
    assert native["origin"] == "native", marker
    db_path = scene.state / scene.profile["paths"]["database"]
    with sqlite3.connect(db_path) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        version = db.execute("PRAGMA user_version").fetchone()[0]
        rows = db.execute("SELECT identity_key,session_id FROM runtime_bindings ORDER BY identity_key").fetchall()
    assert tables == [("runtime_bindings",), ("runtime_cursors",)], marker
    assert version == 1, marker
    assert len(rows) == 2 and len({row[1] for row in rows}) == 2, marker
    task_database = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["database"]
    assert not task_database.exists(), marker


def test_general_placement_without_task_owner(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_PLACEMENT"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    from tests.runtime_services.tool_results_support import Scenario
    fixture = Scenario(adapted(scene), tmp_path)
    policy = json.loads(fixture.placement.read_text())
    assert policy["owner_provider"] is None, marker
    actual = scene.invoke("placement", packet=literal("placement-request.json"), policy=fixture.placement)
    value = json.loads(actual["stdout"])
    assert value["status"] == "ready", marker
    assert len(value["items"]) == 1, marker
    assert value["items"][0]["path"] == "proof.json", marker
    path = Path(value["items"][0]["absolute_path"])
    assert path.name == "proof.json" and path.is_relative_to(fixture.root), marker
    assert not (scene.state / "requirements.sqlite").exists(), marker


def test_general_reader_without_task_owner(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_READER"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    from tests.content_reader.helpers import Scenario
    from tests.hook_transport.helpers import event
    from poise.infrastructure.hook_transport import HookService
    fixture = Scenario(adapted(scene), tmp_path)
    fixture.native_identity("architecture-native")
    fixture.hooks = HookService(scene.settings_path)
    definition, _ = scene.native_definition()
    fixture.installation = fixture.hooks.install({"request_id": "reader-native", "expected_revision": None,
                                                 "definition": definition})
    fixture.event_factory = event
    fixture.data["admission"] = {"hook_settings": str(scene.settings_path),
                                 "definition_path": fixture.installation["definition_path"]}
    fixture.save_policy()
    fixture.event()
    for operation in ("prepare_template", "prepare"):
        observed = scene.invoke("reader", packet={"operation": operation}, policy=fixture.policy,
                                environment=fixture.env)
        value = json.loads(observed["stdout"])
        assert value["status"] == "ready", marker
    resource = Path(value["details"]["path"])
    observed = scene.invoke("reader", packet=fixture.request(start=2, end=3), policy=fixture.policy,
                            environment=fixture.env)
    value = json.loads(observed["stdout"])
    assert value["items"][0]["content"] == "βeta\nlast", marker
    assert value["items"][0]["range"] == "lines:2-3", marker
    fixture.event("SessionEnd")
    recovered = scene.invoke("reader", packet={"operation": "recover"}, policy=fixture.policy,
                             environment=fixture.env)
    assert json.loads(recovered["stdout"])["status"] == "cleaned", marker
    assert not resource.exists(), marker


def test_general_named_checks_and_cli_without_task_owner(project, tmp_path, monkeypatch):
    marker = "ARCH_FIX_GENERAL_CHECKS"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    from tests.named_checks.support import Scene
    fixture = Scene(project, tmp_path, monkeypatch)
    environment = {**scene.env, "F07_FIXTURE_EVENTS": str(fixture.events)}
    fixture.child("--stdout", "ARCHITECTURE_CHECK_OUTPUT\n")
    fixture.policy["storage"]["directory"] = str(scene.state / "named-checks")
    fixture.save()
    machine = scene.invoke("checks", packet=fixture.request(), policy=fixture.policy_path,
                           environment=environment)
    value = json.loads(machine["stdout"])
    assert value["status"] == "passed" and machine["stderr"] == "", marker
    record = fixture.record(value["items"][0])
    assert Path(record["stdout"]["path"]).read_bytes() == b"ARCHITECTURE_CHECK_OUTPUT\n", marker
    assert Path(value["response_path"]).is_file(), marker
    cli = scene.invoke("checks-cli", packet=fixture.request(), policy=fixture.policy_path,
                       environment=environment)
    shown = json.loads(cli["stdout"])
    assert shown["status"] == "passed", marker
    # Second call reuses the completed real check, not its child process.
    assert len(fixture.markers()) == 1, marker


def test_general_tool_result_and_cli_without_task_owner(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_RESULT"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    from tests.runtime_services.tool_results_support import Scenario
    fixture = Scenario(adapted(scene), tmp_path)
    packet = fixture.request()
    machine = scene.invoke("tool-result", packet=packet, policy=fixture.policy_path, environment=fixture.env)
    value = json.loads(machine["stdout"])
    assert value["status"] == "stored", marker
    assert value["response"] == packet["response"], marker
    saved = json.loads(Path(value["response_path"]).read_text())
    assert saved["response"] == packet["response"], marker
    assert Path(saved["streams"]["stdout"]["path"]).read_bytes() == fixture.out.read_bytes(), marker
    assert Path(saved["streams"]["stderr"]["path"]).read_bytes() == fixture.err.read_bytes(), marker
    cli = scene.invoke("tool-result-cli", packet=packet, policy=fixture.policy_path, environment=fixture.env)
    shown = json.loads(cli["stdout"])
    assert shown["status"] == "stored" and shown["response_path"] == value["response_path"], marker


def test_native_lifecycle_without_task_adapter(project, tmp_path):
    marker = "ARCH_FIX_GENERAL_NATIVE"
    scene = GeneralScene(project, tmp_path, marker)
    scene.publish()
    definition, events, builtin_inputs = scene.common_builtin_route()
    environment = {k: v for k, v in scene.env.items() if k != "POISE_CALLER_BINDING"}
    environment["CODEX_THREAD_ID"] = "architecture-native"
    result = scene.invoke("native-builtins", environment=environment, settings=str(scene.settings_path),
                          definition=definition, events=events, builtin_inputs=builtin_inputs)
    assert len(result["responses"]) == 9, marker
    assert [row[1] for row in result["event_rows"]] == [
        "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "PreCompact", "PostCompact", "SessionStart", "Stop", "SessionEnd"], marker
    assert not result["binding"].get("launcher"), marker
    assert result["diagnostic"]["native_event"]["association"]["status"] == "confirmed", marker
    assert result["readiness"]["ready"] is True, marker
    observations = result["readiness"]["observations"]
    assert len(observations) == 1 and observations[0]["status"] == "available", marker
    assert observations[0]["observation"] == "command_executed", marker
    assert Path(observations[0]["receipt"]).is_file(), marker
    assert result["commands"] == [{"actual_exit_code": 0, "stdout": "A01\nA02\n", "stderr": ""}], marker
    before, after = result["coverage_before"], result["coverage_after"]
    assert before["context"]["generation"] == 0 and after["context"]["generation"] == 1, marker
    assert before["details"]["counts"]["pending"] == 0, marker
    assert before["details"]["counts"]["settled"] == 1, marker
    assert [(row["path"], row["ranges"]) for row in before["details"]["documents"]] == [
        ("docs/sample.txt", [[1, 2]])], marker
    from tests.context_resume.helpers import card
    start_cards = [card(row["response"]) for row in result["responses"]
                   if row["event"]["hook_event_name"] == "SessionStart"]
    assert len(start_cards) == 2, marker
    assert start_cards[0]["generation"]["current"] == 0, marker
    assert start_cards[1]["generation"] == {"before": 0, "current": 1}, marker
    assert start_cards[1]["checkpoint"]["state"] == "restored", marker
    assert start_cards[1]["work"]["status"] == start_cards[1]["check"]["status"] == "not_configured", marker
    assert len(result["checkpoints"]) == 1, marker
    checkpoint = result["checkpoints"][0]
    assert checkpoint["schema"] == "poise-work-context-checkpoint-1", marker
    assert checkpoint["event"]["kind"] == "PreCompact", marker
    assert checkpoint["captured"]["coverage"]["generation"] == 0, marker
    consumers = result["consumer_observations"]
    assert len(consumers) == 3, marker
    assert sorted(row["event_receipt"]["event"] for row in consumers) == [
        "SessionEnd", "SessionStart", "SessionStart"], marker
    for row in consumers:
        assert row["actual_exit_code"] == 0 and row["stderr"] == "", marker
        assert row["sentinel"] == {"task_imports": [], "poise_constructions": 0, "current_task_calls": 0}, marker
        receipt = row["event_receipt"]
        assert receipt["binding_id"] == result["binding"]["session_id"], marker
        assert receipt["project_id"] == project["cfg"]["project"], marker
    assert result["diagnostic"]["native_event"]["id"] == result["event_rows"][-1][0], marker
    hook_db = project["root"] / "architecture-hook-control/hook-state.sqlite"
    assert b"PRIVATE_ARCHITECTURE_PROMPT" not in hook_db.read_bytes(), marker
    assert b"PRIVATE_ARCHITECTURE_ASSISTANT" not in hook_db.read_bytes(), marker


def test_connected_task_adapter_preserves_identity_and_anchors(project, tmp_path):
    marker = "ARCH_FIX_TASK_ADAPTER"
    scene = GeneralScene(project, tmp_path, marker, connected=True)
    scene.publish()
    definition, events = scene.native_definition(all_events=True, task=True)
    # Same native prompt receipt is observed twice: accounting must remain once.
    events.insert(2, deepcopy(events[1]))
    environment = {k: v for k, v in scene.env.items() if k != "POISE_CALLER_BINDING"}
    environment["CODEX_THREAD_ID"] = "architecture-native"
    result = scene.invoke("native", allow_task=True, environment=environment,
        settings=str(scene.settings_path), definition=definition, events=events, task=project["task"])
    assert result["sentinel"]["poise_constructions"] > 0, marker
    assert result["work"]["task"], marker
    assert result["messages"]["interaction"]["observed_messages_count"] == 1, marker
    binding = result["binding"]
    assert binding["external_session"] == "architecture-native", marker
    assert binding["launcher"] in json.dumps(result["responses"][0]), marker
    association = result["diagnostic"]["native_event"]["association"]
    assert association["status"] == "confirmed", marker
    assert association["external_session"] == "architecture-native", marker
    assert association["project_id"] == project["cfg"]["project"], marker
    assert result["diagnostic"]["native_event"]["session_id"] == binding["session_id"], marker
    rows = result["event_rows"]
    assert rows[0][1] == "SessionStart" and rows[-1][1] == "SessionEnd", marker
    assert result["diagnostic"]["native_event"]["id"] == rows[-1][0] > rows[0][0], marker
    database = scene.state / scene.profile["paths"]["database"]
    assert b"PRIVATE_ARCHITECTURE_PROMPT" not in database.read_bytes(), marker
    assert b"PRIVATE_ARCHITECTURE_ASSISTANT" not in database.read_bytes(), marker


def test_profile_transition_preserves_task_data_and_authority(project, tmp_path):
    marker = "ARCH_FIX_PROFILE_TRANSITION"
    from poise.runtime import Poise
    from poise.application.work import WorkTools
    from tests.conftest import DeterministicClock
    from tests.batch.helpers import request
    runtime = Poise(project["config_path"], "architecture-existing-task", DeterministicClock())
    started = WorkTools(runtime).invoke(request("bootstrap", {
        "task": project["task"], "decision": None, "feedback": None, "rework_stage": None}))
    assert started["task"], marker
    database = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["database"]

    def task_rows():
        with sqlite3.connect(database) as db:
            return [db.execute(query).fetchall() for query in (
                "SELECT id,status,claimed_by,version,metadata FROM tasks ORDER BY id",
                "SELECT id,task_id FROM sessions ORDER BY id",
                "SELECT seq,task_id,version,data FROM task_events ORDER BY seq")]

    before = task_rows()
    scene = GeneralScene(project, tmp_path, marker)
    unknown = literal("unknown-config-options.json")
    scene.profile.update(unknown["root"])
    scene.profile["limits"].update(unknown["nested"]["limits"])
    packet, first = scene.publish()
    replay = scene.owner.apply(packet)
    assert replay["revision"] == first["revision"], marker
    assert task_rows() == before, marker
    assert json.loads(scene.path.read_text()) == scene.profile, marker
    old_revision = scene.revision
    scene.profile.update(literal("unknown-config-options-changed.json")["root"])
    _, second = scene.publish(request_id="unknown-value-only")
    assert second["revision"] != old_revision, marker
    assert task_rows() == before, marker
    current = scene.path.read_bytes()
    from poise.infrastructure.projects import ProjectSettings
    registry = ProjectSettings(scene.settings_owner).registry
    registry_bytes = registry.read_bytes()
    for problem in ("stale", "extra-envelope", "invalid-profile"):
        invalid = scene.transition_packet(problem, revision=old_revision if problem == "stale" else scene.revision)
        if problem == "extra-envelope":
            invalid["unexpected"] = "strict packet"
        if problem == "invalid-profile":
            invalid["profile"]["git"]["repository"] = str(tmp_path / "not-a-checkout")
        with pytest.raises(PoiseError):
            scene.owner.apply(invalid)
        assert scene.path.read_bytes() == current, marker
        assert registry.read_bytes() == registry_bytes, marker
        assert project["config_path"].read_bytes() == scene.initial_task_bytes, marker
        assert task_rows() == before, marker
