"""Full Poise profile through the accepted v2 supply and actual resources."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
from string import Template
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
IDENTITIES = json.loads((FIXTURES / "catalog-v2-identities.json").read_text())
DATABASES = json.loads((FIXTURES / "catalog-v2-databases.json").read_text())
DIRECTORIES = json.loads((FIXTURES / "catalog-v2-directories.json").read_text())
RETAINED = json.loads((FIXTURES / "catalog-v2-retained-rows.json").read_text())
EXAMPLE = "config/maintenance/environment-maintenance.example.json"
ENVIRONMENT = dict(os.environ, PYTHONPATH=str(REPO / "src"),
                   PYTHONDONTWRITEBYTECODE="1", LANG="C.UTF-8")

_support_spec = importlib.util.spec_from_file_location(
    "p2_generic_test_support", FIXTURES / "generic_v2_support.py")
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
consumer = _support.consumer


def invoke(argv, *, cwd=REPO, env=None):
    return subprocess.run(list(map(str, argv)), cwd=cwd,
                          env=ENVIRONMENT if env is None else env,
                          capture_output=True, text=True)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def snapshot(root):
    """Independent complete byte/inventory observation, not product hashes."""
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def source(tmp_path):
    """Declared source assets; no ignored working operator config copied."""
    selected = tmp_path / "selected-supply"
    shutil.copytree(REPO / "config", selected / "config")
    settings = read_json(selected / "config/project-setup.example.json")
    blueprint = read_json(selected / "config/project-templates/linux-reference.json")
    for reference in blueprint["workflow_sources"].values():
        relative = reference["path"]
        original = REPO / relative.removeprefix("app/")
        destination = selected / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original.read_bytes())
        serialized = json.dumps(read_json(original), ensure_ascii=False,
                                sort_keys=True, separators=(",", ":"))
        reference["digest"] = hashlib.sha256(serialized.encode()).hexdigest()
    write_json(selected / "config/project-templates/linux-reference.json", blueprint)
    settings["root"] = str(selected)
    # A valid test-owned selection for the old public materializer. Digest is
    # an input provenance value, never an expected materialization result.
    encoded = json.dumps(blueprint, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    settings["templates"] = {"linux-reference": {
        "path": "config/project-templates/linux-reference.json",
        "digest": hashlib.sha256(encoded.encode()).hexdigest(),
    }}
    write_json(selected / "config/project-setup.json", settings)
    return selected


@pytest.fixture
def installation(tmp_path, source):
    repository = tmp_path / "repository"
    repository.mkdir()
    for args in (("init", "-b", "main"),
                 ("-c", "user.name=Acceptance", "-c",
                  "user.email=acceptance@example.invalid", "commit",
                  "--allow-empty", "-m", "Isolated repository")):
        result = invoke(["git", "-C", repository, *args])
        assert result.returncode == 0, result.stderr
    return {"root": tmp_path, "source": source, "repository": repository,
            "profile": tmp_path / "profile", "state": tmp_path / "state"}


def materialize(case, *, source=None, project="acceptance", state=None):
    return invoke([sys.executable, "-B", "-m", "poise_environment.profile",
                   "--source", source or case["source"],
                   "--output", case["profile"],
                   "--state", state or case["state"],
                   "--repository", case["repository"],
                   "--project", project, "--base-ref", "main",
                   "--author-name", "Acceptance", "--author-email",
                   "acceptance@example.invalid", "--module-path", REPO / "src"])


@pytest.fixture
def consumer_python(consumer):
    """Reuse the owning fixture's supply and installed-byte checks."""
    return str(consumer)


@pytest.fixture
def deployed(installation):
    result = materialize(installation)
    assert result.returncode == 0, (result.stdout, result.stderr)
    manifest = installation["profile"] / "catalog.json"
    assert manifest.is_file(), "Public materializer must publish the required v2 profile"
    return installation


def consume(case, python, operation="check", *options):
    result = invoke([python, "-I", "-B", "-m", "environment_maintenance",
                     operation, "--catalog-dir", case["profile"],
                     "--set", "python=" + str(python), *options])
    report = json.loads(result.stdout)
    assert report["schema"] == "environment-maintenance/result/v2"
    assert result.returncode == report["exit_code"]
    return result, report


def ready(case, python):
    result, report = consume(case, python, "run")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert report["status"] == "ready"
    assert [row["id"] for row in report["requirements"]] == IDENTITIES
    assert all(row["state"] == "satisfied" for row in report["requirements"])
    return report


def arrange_retained_rows(case):
    task, requirement, telemetry = (RETAINED[name] for name in ("task", "requirement", "telemetry"))
    with sqlite3.connect(case["state"] / "state.sqlite") as db:
        db.execute(
            "INSERT INTO tasks(id,status,stage_index,iteration,version,metadata) VALUES(?,?,?,?,?,?)",
            (task["id"], task["status"], 0, 1, 1, task["metadata"]))
    with sqlite3.connect(case["state"] / "database/requirements.sqlite") as db:
        db.execute("INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)",
                   (requirement["id"], requirement["level"], requirement["status"], requirement["text"]))
    with sqlite3.connect(case["state"] / "telemetry/events.sqlite") as db:
        db.execute("INSERT INTO interaction_events(id,project,data,received_at) VALUES(?,?,?,?)",
                   (telemetry["id"], telemetry["project"], telemetry["data"], telemetry["received_at"]))


def assert_retained_rows(case):
    task, requirement, telemetry = (RETAINED[name] for name in ("task", "requirement", "telemetry"))
    with sqlite3.connect(case["state"] / "state.sqlite") as db:
        assert db.execute("SELECT id,status,metadata FROM tasks WHERE id=?", (task["id"],)).fetchall() == [
            (task["id"], task["status"], task["metadata"])]
    with sqlite3.connect(case["state"] / "database/requirements.sqlite") as db:
        assert db.execute("SELECT id,level,status,text FROM requirements WHERE id=?",
                          (requirement["id"],)).fetchall() == [
            (requirement["id"], requirement["level"], requirement["status"], requirement["text"])]
    with sqlite3.connect(case["state"] / "telemetry/events.sqlite") as db:
        assert db.execute("SELECT id,project,data,received_at FROM interaction_events WHERE id=?",
                          (telemetry["id"],)).fetchall() == [
            (telemetry["id"], telemetry["project"], telemetry["data"], telemetry["received_at"])]


def action(case, python, identifier, role):
    """Execute the exact declared action with the standard template renderer."""
    catalog = read_json(case["profile"] / "catalog.json")
    values = {name: item["default"] for name, item in catalog["parameters"].items()
              if "default" in item}
    values["python"] = python
    declared = next(item for item in catalog["requirements"]
                    if item["id"] == identifier)[role]
    argv = [Template(value).substitute(values) for value in declared["argv"]]
    environment = {name: os.environ[name] for name in catalog["inherit_env"]
                   if name in os.environ}
    environment.update({name: Template(value).substitute(values)
                        for name, value in declared["env"].items()})
    cwd = Path(Template(declared["cwd"]).substitute(values))
    if not cwd.is_absolute():
        cwd = case["profile"] / cwd
    result = invoke(argv, cwd=cwd, env=environment)
    response = json.loads(result.stdout)
    assert response["schema"] == declared["output"]["schema"]
    assert response["requirement_id"] == identifier
    assert response["action"] == role
    return result, response


def test_first_public_materialization_produces_full_v2_profile(installation):
    result = materialize(installation)
    assert result.returncode == 0, (result.stdout, result.stderr)
    manifest = installation["profile"] / "catalog.json"
    assert manifest.is_file(), "Public materializer must publish catalog/v2"
    catalog = read_json(manifest)
    assert catalog["schema"] == "environment-maintenance/catalog/v2"
    assert [item["id"] for item in catalog["requirements"]] == IDENTITIES
    for item in catalog["requirements"]:
        assert set(item) == {"id", "check", "install", "repair"}
        for role in ("check", "install", "repair"):
            assert set(item[role]) == {"argv", "cwd", "env", "output",
                                       "max_output_bytes", "execution_bound_seconds"}
    assert (REPO / EXAMPLE).is_file()
    assert not installation["state"].exists()


def test_repeat_materialization_publishes_selected_parameters(installation):
    first = materialize(installation)
    assert first.returncode == 0, (first.stdout, first.stderr)
    second = materialize(installation, project="second-installation")
    assert second.returncode == 0, (second.stdout, second.stderr)
    catalog = read_json(installation["profile"] / "catalog.json")
    assert catalog["parameters"]["project_id"]["default"] == "second-installation"
    assert not installation["state"].exists()


def test_declared_supplied_example_and_parameter_selection(deployed, consumer_python):
    catalog = read_json(deployed["source"] / EXAMPLE)
    catalog["parameters"]["directory_mode"]["default"] = "0700"
    write_json(deployed["source"] / EXAMPLE, catalog)
    result = invoke(["git", "-C", deployed["repository"], "branch", "selected-second-base"])
    assert result.returncode == 0, result.stderr
    second_state = deployed["root"] / "selected-state"
    result = materialize(deployed, state=second_state, project="selected-project")
    assert result.returncode == 0, (result.stdout, result.stderr)
    generated = read_json(deployed["profile"] / "catalog.json")
    assert generated["parameters"]["state_root"]["default"] == str(second_state)
    assert generated["parameters"]["project_id"]["default"] == "selected-project"
    assert generated["parameters"]["directory_mode"]["default"] == "0700"
    # CLI's explicit base-ref is the operator selection, over example defaults.
    assert generated["parameters"]["base_ref"]["default"] == "main"
    result, report = consume(deployed, consumer_python, "run")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert report["parameters"]["state_root"] == str(second_state)
    assert (second_state / "state.sqlite").is_file()
    assert second_state.stat().st_mode & 0o777 == 0o700
    assert not deployed["state"].exists()


def test_missing_declared_parameter_fails_without_servicing(deployed, consumer_python):
    manifest = deployed["profile"] / "catalog.json"
    catalog = read_json(manifest)
    catalog["parameters"]["state_root"] = {"type": "string"}
    write_json(manifest, catalog)
    before = snapshot(deployed["root"])
    result = invoke([consumer_python, "-I", "-B", "-m", "environment_maintenance",
                     "run", "--catalog-dir", deployed["profile"]])
    assert result.returncode != 0
    assert "state_root" in result.stderr
    assert snapshot(deployed["root"]) == before


def test_bootstrap_dependencies_infrastructure_then_check(deployed, consumer_python):
    config = read_json(REPO / "config/bootstrap.example.json")
    values = deployed["root"] / "consumer-values.json"
    write_json(values, {"python": consumer_python})
    config["parameters"] = {"python": consumer_python, "tool_config": str(values)}
    configured_command = deployed["root"] / "bootstrap.json"
    write_json(configured_command, config)
    for operation in ("deps", "infra", "check"):
        result = invoke([sys.executable, "-B", REPO / "bootstrap", operation,
                         "--config", configured_command, "--catalog-dir", deployed["profile"]])
        packet = json.loads(result.stdout)
        assert result.returncode == 0 and packet["status"] == "ready", packet
        observed = json.loads(packet["observations"][-1]["stdout"])
        assert observed["schema"] == "environment-maintenance/result/v2"
        assert [row["id"] for row in observed["requirements"]] == IDENTITIES
        assert all(row["state"] == "satisfied" for row in observed["requirements"])
    ready(deployed, consumer_python)


@pytest.mark.parametrize("item", DIRECTORIES, ids=lambda item: item["id"])
def test_directory_invalid_mode_is_repaired(deployed, consumer_python, item):
    ready(deployed, consumer_python)
    path = deployed["state"] / item["path"]
    assert path.is_dir()
    path.chmod(0o700)
    result, report = consume(deployed, consumer_python, "run")
    assert result.returncode == 0, (result.stdout, result.stderr)
    row = next(row for row in report["requirements"] if row["id"] == item["id"])
    assert row["initial_state"] == "invalid"
    assert row["mutation"]["response"]["action"] == "repair"
    assert row["state"] == "satisfied"
    assert row["initial_check"]["invocation"] == row["final_check"]["invocation"]
    assert row["final_check"]["response"]["state"] == "satisfied"
    assert path.stat().st_mode & 0o777 == item["mode"]


def test_first_service_creates_real_owner_databases(deployed, consumer_python):
    ready(deployed, consumer_python)
    for item in DATABASES:
        path = deployed["state"] / item["path"]
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            assert db.execute("PRAGMA user_version").fetchone() == (item["version"],)
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert item["table"] in tables
            assert db.execute('SELECT count(*) FROM "' + item["table"] + '"').fetchone() == (0,)
    assert read_json(deployed["profile"] / "project/project.json")["project"] == "acceptance"
    for item in DIRECTORIES:
        path = deployed["state"] / item["path"]
        assert path.is_dir()
        assert path.stat().st_mode & 0o777 == item["mode"]


def test_each_directory_missing_install_and_check_has_selected_effect(deployed, consumer_python):
    assert not deployed["state"].exists()
    for item in DIRECTORIES:
        selected = deployed["state"] / item["path"]
        assert not selected.exists()
        result, response = action(deployed, consumer_python, item["id"], "check")
        assert result.returncode == 0 and response["state"] == "missing"
        result, response = action(deployed, consumer_python, item["id"], "install")
        assert result.returncode == 0 and response["outcome"] == "completed"
        assert selected.is_dir()
        assert selected.stat().st_mode & 0o777 == item["mode"]
        result, response = action(deployed, consumer_python, item["id"], "check")
        assert result.returncode == 0 and response["state"] == "satisfied"
    assert not (deployed["root"] / "other-state").exists()


@pytest.mark.parametrize("identifier", IDENTITIES)
def test_each_declared_action_is_real_and_idempotent(deployed, consumer_python, identifier):
    ready(deployed, consumer_python)
    before = snapshot(deployed["state"])
    for role in ("check", "install", "repair"):
        result, response = action(deployed, consumer_python, identifier, role)
        assert result.returncode == 0, (result.stdout, result.stderr)
        if role == "check":
            assert response["state"] == "satisfied"
        else:
            assert response["outcome"] == "completed"
    assert snapshot(deployed["state"]) == before


def test_repeat_and_refused_repeat_preserve_published_profile_and_data(deployed, consumer_python):
    ready(deployed, consumer_python)
    arrange_retained_rows(deployed)
    assert_retained_rows(deployed)
    marker = deployed["state"] / "operator-data.txt"
    marker.write_text("Operator-owned durable data")
    data_before = snapshot(deployed["state"])
    result = materialize(deployed)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert snapshot(deployed["state"]) == data_before
    assert_retained_rows(deployed)
    ready(deployed, consumer_python)
    profile_before = snapshot(deployed["profile"])
    data_before = snapshot(deployed["state"])
    (deployed["source"] / EXAMPLE).write_text("{invalid")
    failed = materialize(deployed)
    assert failed.returncode != 0
    assert failed.stdout or failed.stderr
    assert snapshot(deployed["profile"]) == profile_before
    assert snapshot(deployed["state"]) == data_before
    assert_retained_rows(deployed)
    result, report = consume(deployed, consumer_python)
    assert result.returncode == 0 and report["status"] == "ready"
    for item in DATABASES:
        with sqlite3.connect(deployed["state"] / item["path"]) as db:
            assert db.execute("PRAGMA user_version").fetchone() == (item["version"],)


def test_repeat_materialization_preserves_working_profile_paths(deployed, consumer_python):
    ready(deployed, consumer_python)
    arrange_retained_rows(deployed)
    expected = read_json(FIXTURES / "catalog-v2-retained-profile.json")
    profile = deployed["profile"]
    catalogue_settings = profile / "config/catalogue/settings.json"
    editor_settings = profile / "config/catalogue/editor.json"
    settings = read_json(catalogue_settings)
    settings["max_items"] = expected["catalogue_max_items"]
    write_json(catalogue_settings, settings)
    editor = read_json(editor_settings)
    editor["output_chars"] = expected["editor_output_chars"]
    write_json(editor_settings, editor)
    editor_state = profile / "editor-state"
    editor_state.mkdir(exist_ok=True)
    (editor_state / "operator-note.txt").write_text(
        expected["editor_note"], encoding="utf-8")
    project_before = snapshot(profile / "project")
    registry_before = (profile / "configured-projects.json").read_bytes()
    editor_before = snapshot(editor_state)
    settings_before = catalogue_settings.read_bytes()
    editor_settings_before = editor_settings.read_bytes()
    data_before = snapshot(deployed["state"])

    selected_catalogue = read_json(deployed["source"] / EXAMPLE)
    selected_catalogue["parameters"]["directory_mode"]["default"] = expected["directory_mode"]
    write_json(deployed["source"] / EXAMPLE, selected_catalogue)
    result = materialize(deployed)

    assert result.returncode == 0, (result.stdout, result.stderr)
    published = read_json(profile / "catalog.json")
    assert published["parameters"]["directory_mode"]["default"] == "0700"
    assert snapshot(profile / "project") == project_before
    assert (profile / "configured-projects.json").read_bytes() == registry_before
    assert snapshot(editor_state) == editor_before
    assert catalogue_settings.read_bytes() == settings_before
    assert editor_settings.read_bytes() == editor_settings_before
    assert read_json(catalogue_settings)["max_items"] == 73
    assert read_json(editor_settings)["output_chars"] == 1800
    assert snapshot(deployed["state"]) == data_before
    assert_retained_rows(deployed)


def test_malformed_selected_project_configuration_is_preserved(deployed, consumer_python):
    ready(deployed, consumer_python)
    arrange_retained_rows(deployed)
    config = deployed["profile"] / "project/project.json"
    config.write_text("{malformed selected operator configuration")
    before = snapshot(deployed["root"])
    result, report = consume(deployed, consumer_python, "run")
    assert result.returncode != 0 and report["status"] != "ready"
    row = next(row for row in report["requirements"] if row["id"] == "poise-project-config")
    response = row["initial_check"]["response"]
    assert response["state"] == "unavailable"
    assert response["cause"] == "invalid_config"
    assert response["recommendations"]
    for role in ("install", "repair"):
        direct, _ = action(deployed, consumer_python, "poise-project-config", role)
        assert direct.returncode != 0
    assert snapshot(deployed["root"]) == before
    assert_retained_rows(deployed)


@pytest.mark.parametrize("item", DATABASES, ids=lambda value: value["id"])
@pytest.mark.parametrize("damage", ["busy", "corrupt", "version", "schema",
                                    "state_drift", "database_drift"])
def test_database_failure_matrix_is_non_destructive(deployed, consumer_python, item, damage):
    ready(deployed, consumer_python)
    path = deployed["state"] / item["path"]
    config_path = deployed["profile"] / "project/project.json"
    writer = None
    if damage == "busy":
        writer = sqlite3.connect(path)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE operator_probe(value TEXT)")
        writer.commit()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO operator_probe(value) VALUES (?)", ("pending-owned-data",))
    elif damage == "corrupt":
        path.write_bytes(b"This is test-owned non-SQLite database input")
    elif damage in ("version", "schema"):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE operator_probe(value TEXT)")
            db.execute("INSERT INTO operator_probe(value) VALUES (?)", ("retained-owned-data",))
            if damage == "version":
                db.execute("PRAGMA user_version=999")
    else:
        config = read_json(config_path)
        outside = deployed["root"] / "other-state"
        outside.mkdir()
        (outside / "keep.txt").write_text("Unrelated operator data")
        if damage == "state_drift":
            config["paths"]["state"] = str(outside)
        elif item["selector"] == "accounting":
            config["accounting"]["storage"]["database"] = str(outside / "outside.sqlite")
        else:
            config["paths"][item["selector"]] = str(outside / "outside.sqlite")
        write_json(config_path, config)
    before = snapshot(deployed["root"])
    try:
        result, report = consume(deployed, consumer_python, "run")
        assert result.returncode != 0 and report["status"] != "ready"
        row = next(row for row in report["requirements"] if row["id"] == item["id"])
        response = row["initial_check"]["response"]
        assert response["state"] == "unavailable"
        expected = "configuration_drift" if damage.endswith("drift") else damage
        assert response["cause"] == expected
        assert response["recommendations"]
        for role in ("install", "repair"):
            direct, _ = action(deployed, consumer_python, item["id"], role)
            assert direct.returncode != 0
        assert snapshot(deployed["root"]) == before
        if writer is not None:
            assert writer.execute("SELECT value FROM operator_probe").fetchall() == [("pending-owned-data",)]
        elif damage in ("schema", "version"):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                assert db.execute("SELECT value FROM operator_probe").fetchall() == [("retained-owned-data",)]
                expected_version = 999 if damage == "version" else item["version"]
                assert db.execute("PRAGMA user_version").fetchone() == (expected_version,)
    finally:
        if writer is not None:
            writer.rollback()
            writer.close()


def test_check_dry_run_and_repeat_service(deployed, consumer_python):
    before = snapshot(deployed["root"])
    for operation, options in (("check", ()), ("run", ("--dry-run",))):
        result, report = consume(deployed, consumer_python, operation, *options)
        assert result.returncode != 0
        assert report["status"] != "ready"
        assert all("mutation" not in row for row in report["requirements"])
        assert snapshot(deployed["root"]) == before
    ready(deployed, consumer_python)
    before = snapshot(deployed["root"])
    ready(deployed, consumer_python)
    assert snapshot(deployed["root"]) == before


@pytest.mark.parametrize("parameter,value,identifier", [
    ("git", "/nonexistent/p002/git", "git-executable"),
    ("base_ref", "nonexistent-p002-base", "target-repository"),
])
def test_unavailable_external_dependency_is_not_ready(deployed, consumer_python,
                                                      parameter, value, identifier):
    ready(deployed, consumer_python)
    before = snapshot(deployed["root"])
    result, report = consume(deployed, consumer_python, "run", "--set", parameter + "=" + value)
    assert result.returncode != 0 and report["status"] != "ready"
    row = next(row for row in report["requirements"] if row["id"] == identifier)
    assert row["initial_check"]["response"]["state"] == "unavailable"
    assert row["initial_check"]["response"]["cause"]
    assert row["initial_check"]["response"]["recommendations"]
    assert snapshot(deployed["root"]) == before


def test_published_profile_catalogue_references_are_consumable(deployed, consumer_python):
    # Explicit integration boundary: declared profile reference -> real owner.
    from poise.infrastructure.catalogue import FileCatalogue

    ready(deployed, consumer_python)
    config = read_json(deployed["profile"] / "project/project.json")
    before = snapshot(deployed["root"])
    catalogue = FileCatalogue(Path(config["task_planning"]["catalogue"]))
    assert catalogue.task_selection("development") == {"id": "development-v1"}
    assert catalogue.workflow_selection("development") == {"id": "development-v1"}
    assert catalogue.task_blueprint({"id": "development-v1"}).goal_type == "development"
    assert catalogue.workflow_template({"id": "development-v1"})["goal_type"] == "development"
    assert catalogue.configured_workflow("development")["goal_type"] == "development"
    assert snapshot(deployed["root"]) == before

    settings_path = catalogue.path
    editor_path = catalogue.editor.path
    settings = read_json(settings_path)
    editor = read_json(editor_path)
    settings["max_items"] = 777
    editor["max_changes"] = 1111
    write_json(settings_path, settings)
    write_json(editor_path, editor)
    operator_state = deployed["profile"] / "editor-state"
    operator_state.mkdir()
    (operator_state / "operator-note.txt").write_text("Retained operator editor state")
    retained = snapshot(deployed["root"])
    repeated = materialize(deployed)
    assert repeated.returncode == 0, (repeated.stdout, repeated.stderr)
    assert snapshot(deployed["root"]) == retained
    assert read_json(settings_path)["max_items"] == 777
    assert read_json(editor_path)["max_changes"] == 1111
    assert (operator_state / "operator-note.txt").read_text() == "Retained operator editor state"
    catalogue = FileCatalogue(Path(config["task_planning"]["catalogue"]))
    assert catalogue.task_selection("development") == {"id": "development-v1"}
    assert catalogue.workflow_selection("development") == {"id": "development-v1"}
    assert catalogue.task_blueprint({"id": "development-v1"}).goal_type == "development"
