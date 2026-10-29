"""Later GREEN dependency/runtime proof; absence is setup, never feature RED."""

from contextlib import closing
import importlib.metadata
import importlib.util
import sqlite3
import subprocess
from copy import deepcopy

from requirements_registry.sql_migration_support import applied, catalog, connected_to, literal, owned, start, state
from requirements_registry.test_sql_legacy_export import legacy, request
from requirements_registry.test_sql_migration_completion import finish
from requirements_registry.test_sql_migration_initialization import tool


def test_stock_yoyo_multisource_dependencies_and_inverse_without_poise(tmp_path):
    assert importlib.util.find_spec("yoyo") is not None, "SETUP: selected runtime lacks declared yoyo dependency"
    assert importlib.metadata.version("yoyo-migrations") == "9.0.0"
    schema_source, data_source = tmp_path / "schema", tmp_path / "data"
    schema_source.mkdir()
    data_source.mkdir()
    for body in literal("stock_tool.json"):
        directory = schema_source if body["source"] == "schema" else data_source
        (directory / f"{body['id']}.sql").write_text(body["forward"], encoding="utf-8")
        (directory / f"{body['id']}.rollback.sql").write_text(body["reverse"], encoding="utf-8")
    database = tmp_path / "stock.sqlite"

    tool([schema_source, data_source], database, "apply")

    assert applied(database) == ["0001_data", "0010_index", "9000_schema"]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT id,value,typeof(value),hex(value) FROM stock_probe").fetchall() == [(1, "A\x00B", "text", "410042")]
    tool([schema_source, data_source], database, "rollback")
    assert applied(database) == []
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='stock_probe'").fetchall() == []


def test_preexisting_foreign_file_and_applied_history_survive_task_completion(project, monkeypatch):
    assert importlib.util.find_spec("yoyo") is not None, "SETUP: foreign-history arrangement requires declared stock tool"
    body = literal("foreign_history.json")
    settings = literal("contract.json")["settings"]
    foreign = project["app"] / settings["data_directory"]
    foreign.mkdir(parents=True)
    (foreign / f"{body['id']}.sql").write_text(body["forward"], encoding="utf-8")
    (foreign / f"{body['id']}.rollback.sql").write_text(body["reverse"], encoding="utf-8")
    database = project["root"] / project["cfg"]["paths"]["state"] / project["cfg"]["paths"]["requirements_database"]
    tool(foreign, database, "apply")
    subprocess.run(["git", "add", "--", settings["data_directory"]],
                   cwd=project["app"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "fixture: prior Task migration"],
                   cwd=project["app"], check=True, capture_output=True)
    subject = start(project)
    packet = deepcopy(literal("contract.json")["ordinary_create"])
    packet["input"]["expected_revision"] = 2
    assert owned(subject, packet) == {
        "status": "applied", "revision": 3, "replayed": False,
        "results": [{"id": "create", "affected_rows": 1}],
    }
    before = state(database)
    original = sqlite3.connect
    foreign_statements = []

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_trace_callback(self.observe)

        def observe(self, statement):
            if self.target and "'OTHER'" in statement and statement.strip().lower().startswith(("insert ", "delete ", "update ")):
                foreign_statements.append(statement)

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert finish(subject)["status"] == "completed"
    assert foreign_statements == []
    assert state(database) == before
    assert body["id"] in applied(database)
    assert (subject.directory / f"{body['id']}.sql").read_text(encoding="utf-8") == body["forward"]
    assert (subject.directory / f"{body['id']}.rollback.sql").read_text(encoding="utf-8") == body["reverse"]


def test_captured_export_applies_with_stock_tool_to_alternate_compared_target(project):
    assert importlib.util.find_spec("yoyo") is not None, "SETUP: literal export proof requires declared stock tool"
    subject = start(project)
    source = legacy(subject)
    comparison = subject.project["root"] / "future-registry.sqlite"
    tool(subject.initial, comparison, "apply")
    packet = request(subject, source, "full", [])
    packet["input"]["target"] = str(comparison)
    before_live, before_source = state(subject.database), state(source)
    source_catalog, live_catalog = catalog(source), catalog(subject.database)

    generated = owned(subject, packet)
    assert generated["status"] == "generated"
    tool([subject.initial, subject.directory], comparison, "apply")

    assert state(comparison) == before_source
    assert state(subject.database) == before_live
    assert state(source) == before_source
    assert catalog(subject.database) == live_catalog
    assert catalog(source) == source_catalog
