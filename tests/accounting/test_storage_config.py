"""Operator-selected telemetry paths; no implicit file, lock or migration."""
import json
import os
from pathlib import Path

import pytest

from conftest import DeterministicClock, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError, configured_root, load_config
from poise.composition import project_config_tools, project_tools
from poise.modules.accounting.domain import MetricPolicy
from poise.runtime import Poise
from tests.accounting.test_domain import policy
from tests.accounting.test_optional_telemetry_isolation import _raw_telemetry
from tests.batch.helpers import request
from tests.projects.helpers import setup_case


def test_explicit_storage_policy_accepts_operator_paths():
    selected = {"database": "operator/economics.sqlite", "lock": "separate-locks/economics.lock"}
    raw = policy()
    raw["storage"] = selected
    parsed = MetricPolicy.parse(raw)
    assert parsed.data["storage"] == selected


@pytest.mark.parametrize("case", [
    "missing",
    "null",
    "list",
    "missing_database",
    "missing_lock",
    "extra",
    "database_type",
    "lock_type",
    "database_empty",
    "lock_empty",
])
def test_storage_fields_are_required_and_typed(case):
    raw = policy()
    if case == "missing":
        del raw["storage"]
    elif case in {"null", "list"}:
        raw["storage"] = None if case == "null" else []
    elif case.startswith("missing_"):
        del raw["storage"][case.removeprefix("missing_")]
    elif case == "extra":
        raw["storage"]["fallback"] = True
    else:
        field, kind = case.split("_")
        raw["storage"][field] = 7 if kind == "type" else ""
    with pytest.raises(PoiseError, match="storage"):
        MetricPolicy.parse(raw)


@pytest.mark.parametrize("field", ["database", "lock"])
@pytest.mark.parametrize("invalid", ["../escape", "/absolute.sqlite", "."])
def test_storage_paths_must_be_files_inside_explicit_state(project, field, invalid):
    project["cfg"]["accounting"]["storage"][field] = invalid
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError):
        load_config(project["config_path"])


@pytest.mark.parametrize("optional_field", ["database", "lock"])
@pytest.mark.parametrize("authoritative_field", ["database", "lock"])
def test_storage_cannot_alias_any_authoritative_file(project, optional_field, authoritative_field):
    project["cfg"]["accounting"]["storage"][optional_field] = project["cfg"]["paths"][authoritative_field]
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError, match="storage"):
        load_config(project["config_path"])


def test_optional_database_and_lock_must_be_distinct(project):
    storage = project["cfg"]["accounting"]["storage"]
    storage["lock"] = storage["database"]
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError, match="storage"):
        load_config(project["config_path"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("optional_field", ["database", "lock"])
@pytest.mark.parametrize("authoritative_field", ["database", "lock"])
def test_storage_rejects_physical_authoritative_aliases(
    project, kind, optional_field, authoritative_field,
):
    state = configured_root(project["root"], project["cfg"]["paths"]["state"])
    state.mkdir(parents=True)
    authoritative = state / project["cfg"]["paths"][authoritative_field]
    authoritative.write_bytes(b"preserved authoritative file")
    alias = state / f"alias-{optional_field}-{authoritative_field}"
    if kind == "symlink":
        alias.symlink_to(authoritative)
    else:
        os.link(authoritative, alias)
    project["cfg"]["accounting"]["storage"][optional_field] = alias.name
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError):
        load_config(project["config_path"])
    assert authoritative.read_bytes() == b"preserved authoritative file"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("alias_field,target_field", [("database", "lock"), ("lock", "database")])
def test_optional_database_and_lock_reject_physical_aliases(
    project, kind, alias_field, target_field,
):
    state = configured_root(project["root"], project["cfg"]["paths"]["state"])
    state.mkdir(parents=True)
    target = state / project["cfg"]["accounting"]["storage"][target_field]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"preserved optional file")
    alias = state / f"optional-{alias_field}-alias"
    if kind == "symlink":
        alias.symlink_to(target)
    else:
        os.link(target, alias)
    project["cfg"]["accounting"]["storage"][alias_field] = alias.name
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError):
        load_config(project["config_path"])
    assert target.read_bytes() == b"preserved optional file"


@pytest.mark.parametrize("absolute_state", [False, True])
def test_production_composition_uses_exact_configured_paths(project, absolute_state):
    chosen = {"database": "chosen-data/raw.sqlite", "lock": "chosen-locks/raw.lock"}
    project["cfg"]["accounting"]["storage"] = chosen
    if absolute_state:
        project["cfg"]["paths"]["state"] = str(project["root"].parent / "operator-state")
    write_json(project["config_path"], project["cfg"])
    runtime = Poise(project["config_path"], "composition", DeterministicClock())
    try:
        database = runtime.accounting.port.repo.database
        expected_root = configured_root(project["root"], project["cfg"]["paths"]["state"])
        assert database.path == expected_root / chosen["database"]
        assert database.lock == expected_root / chosen["lock"]
        assert database is not runtime.store.database
    finally:
        runtime.telemetry.dispatcher.close()


def _initial_setup(project):
    project["cfg"]["schema"] = "ddd-accounting-12"
    project["cfg"]["accounting"] = policy()
    settings, _, create = setup_case(project)
    published = project_tools(settings).apply(create)
    selected = project["cfg"]["accounting"]["storage"]
    return settings, Path(published["config_path"]), published, selected


def _legacy_setup(project):
    project["cfg"]["schema"] = "ddd-accounting-11"
    del project["cfg"]["accounting"]["storage"]
    settings, _, create = setup_case(project)
    published = project_tools(settings).apply(create)
    return settings, Path(published["config_path"]), published


def _activation_request(config_path, revision, storage, request_id="activate-telemetry-storage"):
    return {
        "schema": "project-config-update-2",
        "request_id": request_id,
        "config_path": str(config_path),
        "expected_revision": revision,
        "manifest_edits": [],
        "process_updates": [],
        "state_relocation": None,
        "storage_activation": {
            "from_schema": "ddd-accounting-11",
            "to_schema": "ddd-accounting-12",
            "storage": storage,
        },
        "probe_repository": False,
        "receipt_path": "operations/activate-telemetry-storage.json",
    }


def _observe_once(config_path, session):
    runtime = Poise(config_path, session, DeterministicClock())
    packet = request("show", {"queries": [{"id": "task", "kind": "task"}]})
    packet["telemetry"] = _raw_telemetry()
    assert WorkTools(runtime).invoke(packet)["status"] == "read_only"
    runtime.telemetry.dispatcher.close()
    assert len(runtime.accounting.port.repo.snapshot()["telemetry"]) == 1
    return runtime


def test_public_initial_setup_activates_only_explicit_selected_storage(project):
    _, config_path, published, chosen = _initial_setup(project)
    assert published["status"] == "created"
    root, cfg, _ = load_config(config_path)
    assert cfg["accounting"]["storage"] == chosen
    state = configured_root(root, cfg["paths"]["state"])
    assert not (state / chosen["database"]).exists()  # Setup is configuration publication.
    runtime = _observe_once(config_path, "initial-observer")
    assert runtime.accounting.port.repo.database.path == state / chosen["database"]
    assert runtime.accounting.port.repo.database.lock == state / chosen["lock"]


def test_public_quiescent_update_replay_selects_new_store_without_migrating_old(project):
    settings, config_path, published = _legacy_setup(project)
    before = config_path.read_bytes()
    chosen = {"database": "after-update/telemetry.sqlite", "lock": "after-update/telemetry.lock"}
    packet = _activation_request(config_path, published["revision"], chosen)
    result = project_config_tools(settings).apply(packet)
    assert result["status"] == "updated"
    assert result["revision"] != result["prior_revision"]
    replay = project_config_tools(settings).apply(packet)
    assert replay["replayed"] is True and replay["revision"] == result["revision"]
    runtime = _observe_once(config_path, "after-update")
    assert runtime.accounting.port.repo.database.path == runtime.state / chosen["database"]
    assert runtime.accounting.port.repo.database.lock == runtime.state / chosen["lock"]
    assert before != config_path.read_bytes()
    assert json.loads(config_path.read_text())["schema"] == "ddd-accounting-12"
    with runtime.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_public_update_rejects_collision_without_publishing_or_mutating_old_store(project):
    settings, config_path, published = _legacy_setup(project)
    before = config_path.read_bytes()
    _, old_config, _ = load_config(config_path)
    chosen = {"database": old_config["paths"]["database"], "lock": "separate.lock"}
    packet = _activation_request(config_path, published["revision"], chosen)
    with pytest.raises(PoiseError, match="storage"):
        project_config_tools(settings).apply(packet)
    assert config_path.read_bytes() == before


def test_active_schema11_task_remains_operable_until_quiescent_activation(project):
    settings, config_path, published = _legacy_setup(project)
    runtime = Poise(config_path, "legacy-owner", DeterministicClock())
    work = WorkTools(runtime)
    started = work.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert started["status"] == "active"
    packet = _activation_request(
        config_path,
        published["revision"],
        {"database": "activated/events.sqlite", "lock": "activated/events.lock"},
    )
    before = config_path.read_bytes()
    with pytest.raises(PoiseError, match="quiescent|active"):
        project_config_tools(settings).apply(packet)
    assert config_path.read_bytes() == before
    # Rejected activation cannot strand the already-running schema-11 source.
    assert work.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))["status"] == "read_only"
    work.invoke(request("cancel", {"reason": "Make activation test project quiescent"}))
    runtime.telemetry.dispatcher.close()
    activated = project_config_tools(settings).apply(packet)
    assert activated["status"] == "updated"
    assert load_config(config_path)[1]["schema"] == "ddd-accounting-12"
    after = Poise(config_path, "after-activation", DeterministicClock())
    try:
        assert after.task_queries.record(project["task"]["id"])["status"] == "cancelled"
        assert after.accounting.port.repo.database.path == after.state / "activated/events.sqlite"
    finally:
        after.telemetry.dispatcher.close()


def test_schema11_runtime_never_writes_optional_envelope_into_authoritative_database(project):
    _, config_path, _ = _legacy_setup(project)
    runtime = Poise(config_path, "legacy-isolated", DeterministicClock())
    packet = request("show", {"queries": [{"id": "task", "kind": "task"}]})
    packet["telemetry"] = _raw_telemetry()
    result = WorkTools(runtime).invoke(packet)
    runtime.telemetry.dispatcher.close()
    with runtime.store.transaction() as database:
        envelopes = database.execute(
            "SELECT count(*) FROM accounting_cycles WHERE json_extract(data,'$.kind')='telemetry_envelope'"
        ).fetchone()[0]
    assert envelopes == 0
    assert result["interaction"]["coverage"] in {"unavailable", "partial"}


@pytest.mark.parametrize("case", ["wrong_from", "wrong_to", "missing_field", "extra_field"])
def test_invalid_activation_is_atomic_and_keeps_schema11_source_operable(project, case):
    settings, config_path, published = _legacy_setup(project)
    packet = _activation_request(
        config_path,
        published["revision"],
        {"database": "valid/events.sqlite", "lock": "valid/events.lock"},
        request_id=f"invalid-activation-{case}",
    )
    if case == "wrong_from":
        packet["storage_activation"]["from_schema"] = "ddd-accounting-10"
    elif case == "wrong_to":
        packet["storage_activation"]["to_schema"] = "ddd-accounting-13"
    elif case == "missing_field":
        del packet["storage_activation"]["storage"]
    else:
        packet["storage_activation"]["fallback"] = True
    before = config_path.read_bytes()
    with pytest.raises(PoiseError):
        project_config_tools(settings).apply(packet)
    assert config_path.read_bytes() == before
    runtime = Poise(config_path, f"legacy-after-{case}", DeterministicClock())
    try:
        assert WorkTools(runtime).invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))["status"] == "read_only"
    finally:
        runtime.telemetry.dispatcher.close()
