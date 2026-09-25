"""One current schema-12 telemetry storage contract; no migration."""
import json
import os
from pathlib import Path

import pytest

from conftest import DeterministicClock, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError, configured_root, load_config
from poise.composition import project_tools
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


def test_superseded_schema11_is_rejected(project):
    project["cfg"]["schema"] = "ddd-accounting-11"
    write_json(project["config_path"], project["cfg"])
    with pytest.raises(PoiseError, match="Версия конфигурации"):
        load_config(project["config_path"])


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
    state.mkdir(parents=True, exist_ok=True)
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
    state.mkdir(parents=True, exist_ok=True)
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

def _observe_once(config_path, session):
    runtime = Poise(config_path, session, DeterministicClock())
    packet = request("show", {"queries": [{"id": "task", "kind": "task"}]})
    packet["telemetry"] = _raw_telemetry()
    assert WorkTools(runtime).invoke(packet)["status"] == "read_only"
    runtime.telemetry.dispatcher.close()
    assert len(runtime.accounting.port.repo.snapshot()["telemetry"]) == 1
    return runtime


def test_public_initial_setup_uses_only_explicit_selected_storage(project):
    _, config_path, published, chosen = _initial_setup(project)
    assert published["status"] == "created"
    root, cfg, _ = load_config(config_path)
    assert cfg["accounting"]["storage"] == chosen
    state = configured_root(root, cfg["paths"]["state"])
    assert not (state / chosen["database"]).exists()  # Setup is configuration publication.
    runtime = _observe_once(config_path, "initial-observer")
    assert runtime.accounting.port.repo.database.path == state / chosen["database"]
    assert runtime.accounting.port.repo.database.lock == state / chosen["lock"]


@pytest.mark.parametrize(
    "relative,blueprint,accounting_only,selected",
    [
        (
            "config/accounting.example.json",
            False,
            True,
            {"database": "telemetry/events.sqlite", "lock": "telemetry/events.lock"},
        ),
        (
            "config/project.example.json",
            False,
            False,
            {"database": "telemetry/events.sqlite", "lock": "telemetry/events.lock"},
        ),
        (
            "config/project-templates/linux-reference.json",
            True,
            False,
            {"database": "telemetry/events.sqlite", "lock": "telemetry/events.lock"},
        ),
        (
            "config/project-templates/wsl-poise.json",
            True,
            False,
            {"database": "telemetry/events.sqlite", "lock": "telemetry/events.lock"},
        ),
        (
            "config/projects/ai-poise/project.json",
            False,
            False,
            {"database": "database/telemetry.sqlite", "lock": "database/telemetry.lock"},
        ),
    ],
)
def test_repository_config_surfaces_publish_schema12_storage(
    relative, blueprint, accounting_only, selected,
):
    payload = json.loads((Path(__file__).resolve().parents[2] / relative).read_text())
    if accounting_only:
        accounting = payload
    else:
        config = payload["config"] if blueprint else payload
        assert config["schema"] == "ddd-accounting-12"
        accounting = config["accounting"]
    assert accounting["storage"] == selected
