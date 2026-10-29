"""Separate literal schema/data/inverses run through the unmodified stock tool."""

from contextlib import closing
import os
import json
import sqlite3
import subprocess
import sys
import shutil

import pytest

from requirements_registry.sql_migration_support import FIXTURES, LIBRARY_TABLES, SOURCE, literal, schema, state


def tool(directory, database, action):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    directories = directory if isinstance(directory, list) else [directory]
    result = subprocess.run([
        sys.executable, str(FIXTURES / "standalone_driver.py"),
        json.dumps([str(path) for path in directories]), str(database), action,
    ], cwd=database.parent, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def shape(database):
    tables = ["requirements", "domain_areas", "domain_entities", "domain_actions",
              "requirement_links", "requirement_entities", "requirement_actions",
              "requirements_meta", "requirement_requests"]
    with closing(sqlite3.connect(database)) as connection:
        return {table: {
            "columns": connection.execute(f"PRAGMA table_info({table})").fetchall(),
            "foreign_keys": connection.execute(f"PRAGMA foreign_key_list({table})").fetchall(),
            "unique_keys": sorted([
                connection.execute(f"PRAGMA index_info({row[1]})").fetchall()
                for row in connection.execute(f"PRAGMA index_list({table})") if row[2]
            ]),
        } for table in tables}


def test_literal_initial_schema_data_and_inverse_work_without_poise_imports(tmp_path):
    contract = literal("contract.json")
    directory = SOURCE / contract["settings"]["initial_directory"]
    required = {f"{identity}{suffix}" for identity in contract["initial_ids"]
                for suffix in (".sql", ".rollback.sql")}
    # Missing product resources are behavioral RED; a missing dependency is not RED.
    assert directory.is_dir(), "Literal initial SQL migration resources are absent"
    assert required <= {path.name for path in directory.iterdir()}
    assert not list(directory.glob("*.py")), "Initial migration sources must be literal SQL"
    database = tmp_path / "fresh.sqlite"
    oracle = tmp_path / "oracle.sqlite"
    with closing(sqlite3.connect(oracle)) as connection:
        connection.executescript((FIXTURES.parent / "sql_legacy_schema.sql").read_text(encoding="utf-8"))

    schema_source = tmp_path / "schema-only"
    schema_source.mkdir()
    for suffix in (".sql", ".rollback.sql"):
        name = contract["initial_ids"][0] + suffix
        shutil.copyfile(directory / name, schema_source / name)
    tool(schema_source, database, "apply")
    assert shape(database) == shape(oracle)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT revision FROM requirements_meta").fetchall() == []
        assert connection.execute("SELECT id FROM requirements").fetchall() == []

    tool(directory, database, "apply")

    expected = state(oracle)
    assert state(database) == expected
    assert shape(database) == shape(oracle)
    assert {row[1] for row in schema(database) if row[0] == "table"} == set(shape(oracle))
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("BEGIN;\n" + (FIXTURES / "seed.sql").read_text(encoding="utf-8"))
        for statement in literal("constraint_errors.json"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        connection.rollback()
    assert state(database) == expected
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    tool(directory, database, "rollback")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
        assert [name for name in names if name not in LIBRARY_TABLES and not name.startswith("sqlite_")] == []
    tool(directory, database, "apply")
    assert state(database) == expected
    assert shape(database) == shape(oracle)
