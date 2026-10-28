"""Independent GREEN controls: fixture faults reach a real later SQLite error."""

from contextlib import closing
import json
import sqlite3

import pytest

from requirements_registry.sql_migration_support import FIXTURES, literal, schema, state


@pytest.mark.parametrize("kind", ["ddl", "dml", "combined"])
@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_literal_fault_fixture_reaches_later_error_and_has_exact_inverse(tmp_path, kind, direction):
    database = tmp_path / "fixture-control.sqlite"
    fixture = literal("body_faults.json")[kind]
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript((FIXTURES.parent / "sql_legacy_schema.sql").read_text(encoding="utf-8"))
        registered = json.loads(
            (FIXTURES.parent / "sql_fixture_registration.json").read_text(encoding="utf-8"))
        connection.executemany("INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)",
                               registered["requirements"])
        connection.commit()
        connection.executescript((FIXTURES / "seed.sql").read_text(encoding="utf-8"))
        baseline, baseline_schema = state(database), schema(database)
        connection.executescript(fixture["forward"])
        connection.executescript(fixture["reverse"])
        assert state(database) == baseline
        assert schema(database) == baseline_schema
        if direction == "reverse":
            connection.executescript(fixture["forward"])
        before, before_schema = state(database), schema(database)
        traces = []
        connection.set_trace_callback(traces.append)

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript("BEGIN;\n" + fixture[f"failed_{direction}"])
        connection.rollback()

        first = fixture[f"failed_{direction}"].splitlines()[0].rstrip(";")
        later = "CREATE UNIQUE INDEX fault_duplicate" if kind == "ddl" else "'Duplicate','Failure'"
        assert any(first in statement for statement in traces)
        assert any(later in statement for statement in traces)
        assert state(database) == before
        assert schema(database) == before_schema
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        for statement in literal("constraint_errors.json"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        connection.rollback()
        assert state(database) == before
        assert schema(database) == before_schema
