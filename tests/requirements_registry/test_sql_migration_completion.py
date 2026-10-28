"""Task completion must execute SQL; migration marks cannot stand in for bodies."""

from contextlib import closing
import sqlite3
from pathlib import Path

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from runtime_services.stage_criteria_helpers import verify_packet
from requirements_registry.sql_migration_support import (
    applied, connected_to, files, literal, migration_request, owned, pair, schema, start, state,
)


@pytest.fixture
def subject(project):
    return start(project)


def finish(subject):
    subject.tools.invoke(verify_packet(subject.context))
    return subject.tools.invoke(request("accept", {}))


def task_status(subject):
    response = subject.tools.invoke(request("show", {"queries": [{
        "id": "task", "kind": "ownership_recovery", "task_ids": ["T1"],
    }]}))
    return response["results"][0]["value"]["tasks"][0]["status"]


def committed_boundaries(subject, monkeypatch):
    from yoyo.backends.base import DatabaseBackend

    observations = []

    def observer(original, direction):
        def completed(backend, migration, *args, **kwargs):
            result = original(backend, migration, *args, **kwargs)
            if connected_to(backend.connection, subject.database):
                # A separate reader observes committed data after the ordinary call returns.
                with closing(sqlite3.connect(subject.database.as_uri() + "?mode=ro", uri=True)) as connection:
                    exists = connection.execute(
                        "SELECT name FROM sqlite_schema WHERE name='completion_probe'",
                    ).fetchone()
                    probe = (None if exists is None else [list(row) for row in connection.execute(
                        "SELECT id,value FROM completion_probe ORDER BY id")])
                observations.append({"direction": direction, "id": migration.id,
                                     "state": state(subject.database), "schema": schema(subject.database),
                                     "applied": applied(subject.database), "probe": probe})
            return result
        return completed

    monkeypatch.setattr(DatabaseBackend, "apply_one", observer(DatabaseBackend.apply_one, "up"))
    monkeypatch.setattr(DatabaseBackend, "rollback_one", observer(DatabaseBackend.rollback_one, "down"))
    return observations


def test_completion_with_zero_migrations_keeps_requirements_state(subject):
    before = state(subject.database)
    finish(subject)
    assert task_status(subject) == "completed"
    assert state(subject.database) == before
    assert files(subject) == {}


def test_completion_really_reverses_original_set_and_replays_final_set(subject, monkeypatch):
    contract = literal("completion.json")
    expected = literal("completion_states.json")
    assert state(subject.database) == expected["entry"]
    entry_schema = schema(subject.database)
    entry_ids = applied(subject.database)
    ddl = contract["schema"]
    identities = [pair(subject, ddl["id"], ddl["forward"], ddl["reverse"], source="schema")]
    for body in contract["data"]:
        identities.append(pair(subject, body["id"], body["forward"], body["reverse"]))
    owned(subject, migration_request(subject, "apply", identities))
    assert state(subject.database) == expected["final"]
    final_schema = sorted(entry_schema + [expected["probe_schema"]])
    assert schema(subject.database) == final_schema
    assert applied(subject.database) == sorted(entry_ids + identities)
    ddl_before = {name: value for name, value in files(subject).items() if ddl["id"] in name}
    statements = []
    original = sqlite3.connect

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_trace_callback(self.observe)

        def observe(self, statement):
            stripped = statement.strip().rstrip(";")
            if self.target and "completion_probe" in stripped and stripped.upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "DROP ", "CREATE ")):
                statements.append(stripped)

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    boundaries = committed_boundaries(subject, monkeypatch)

    finish(subject)

    assert task_status(subject) == "completed"
    assert statements == contract["expected_sql"]
    assert state(subject.database) == expected["final"]
    assert schema(subject.database) == final_schema
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT id,value FROM completion_probe ORDER BY id").fetchall() == []
    final_files = files(subject)
    assert {name: value for name, value in final_files.items() if ddl["id"] in name} == ddl_before
    data = {name: value for name, value in final_files.items() if ddl["id"] not in name}
    assert len(data) == 2
    assert sorted(value.decode("utf-8") for value in data.values()) == sorted([
        contract["combined_forward"], contract["combined_reverse"],
    ])
    ids = applied(subject.database)
    assert ddl["id"] in ids
    assert not (set(identities[1:]) & set(ids))
    assert len(set(ids) - {ddl["id"]}) == 1
    final_id = Path(next(name for name in data if not name.endswith(".rollback.sql"))).stem
    assert [(event["direction"], event["id"]) for event in boundaries] == [
        ("down", "0013_delete"), ("down", "0012_update"), ("down", "0011_insert"), ("down", ddl["id"]),
        ("up", ddl["id"]), ("up", final_id), ("down", final_id), ("down", ddl["id"]),
        ("up", ddl["id"]), ("up", final_id),
    ]
    assert boundaries[0]["state"] == expected["after_update"]
    assert boundaries[0]["probe"] == [[1, "Changed"]]
    assert boundaries[1]["state"] == expected["after_insert"]
    assert boundaries[1]["probe"] == [[1, "Original"]]
    assert boundaries[2]["state"] == expected["entry"]
    assert boundaries[2]["probe"] == []
    for position in (3, 7):
        assert boundaries[position] == {
            "direction": "down", "id": ddl["id"], "state": expected["entry"],
            "schema": entry_schema, "applied": entry_ids, "probe": None,
        }
    for position in (5, 9):
        assert boundaries[position] == {
            "direction": "up", "id": final_id, "state": expected["final"],
            "schema": final_schema, "applied": sorted(entry_ids + [ddl["id"], final_id]), "probe": [],
        }


def test_completion_with_one_data_pair_executes_down_up_down_up(subject, monkeypatch):
    expected = literal("completion_states.json")
    assert state(subject.database) == expected["entry"]
    entry_schema, entry_ids = schema(subject.database), applied(subject.database)
    owned(subject, literal("contract.json")["ordinary_create"])
    assert state(subject.database) == expected["one_final"]
    statements = []
    original = sqlite3.connect

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_trace_callback(self.observe)

        def observe(self, statement):
            lower = statement.strip().lower()
            if self.target and "domain_areas" in lower and lower.startswith(("insert ", "delete ", "update ")):
                statements.append(lower.split()[0])

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    boundaries = committed_boundaries(subject, monkeypatch)
    assert finish(subject)["status"] == "completed"
    assert statements == ["delete", "insert", "delete", "insert"]
    assert state(subject.database) == expected["one_final"]
    assert len(files(subject)) == 2
    final_id = Path(next(name for name in files(subject) if not name.endswith(".rollback.sql"))).stem
    assert [event["direction"] for event in boundaries] == ["down", "up", "down", "up"]
    for position in (0, 2):
        assert boundaries[position]["state"] == expected["entry"]
        assert boundaries[position]["schema"] == entry_schema
        assert boundaries[position]["applied"] == entry_ids
    for position in (1, 3):
        assert boundaries[position]["state"] == expected["one_final"]
        assert boundaries[position]["schema"] == entry_schema
        assert boundaries[position]["applied"] == sorted(entry_ids + [final_id])


@pytest.mark.parametrize("step", ["original_down", "final_up", "final_down", "last_up", "last_mark"])
def test_completion_failure_never_publishes_task_complete(subject, monkeypatch, step):
    contract = literal("completion.json")
    ddl = contract["schema"]
    identities = [pair(subject, ddl["id"], ddl["forward"], ddl["reverse"], source="schema")]
    for body in contract["data"]:
        identities.append(pair(subject, body["id"], body["forward"], body["reverse"]))
    owned(subject, migration_request(subject, "apply", identities))
    expected = literal("completion_states.json")
    assert state(subject.database) == expected["final"]
    counters = {"body": 0, "mark": 0}
    denied = []
    original = sqlite3.connect
    fail_at = {"original_down": 1, "final_up": 2, "final_down": 3, "last_up": 4}

    class Failing(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_authorizer(self.authorize)

        def authorize(self, action, table, column, database, trigger):
            if not self.target:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_UPDATE and table == "completion_probe":
                counters["body"] += 1
                if counters["body"] == fail_at.get(step):
                    denied.append(step)
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

    def connect(*args, **kwargs):
        kwargs["factory"] = Failing
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    if step == "last_mark":
        from yoyo.backends.base import DatabaseBackend

        original_mark = DatabaseBackend.mark_one

        def fail_last_mark(backend, *args, **kwargs):
            if connected_to(backend.connection, subject.database):
                counters["mark"] += 1
                if counters["mark"] == 4:
                    denied.append(step)
                    raise sqlite3.OperationalError("Test-owned final status failure")
            return original_mark(backend, *args, **kwargs)

        monkeypatch.setattr(DatabaseBackend, "mark_one", fail_last_mark)
    with pytest.raises(PoiseError, match="(?i)manual|repair|recover"):
        finish(subject)
    assert denied == [step]
    assert task_status(subject) != "completed"
    outcome = ("after_update" if step == "original_down" else
               "final" if step in ("final_down", "last_mark") else "entry")
    assert state(subject.database) == expected[outcome]
    assert files(subject)
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT id,value FROM completion_probe ORDER BY id").fetchall() == (
            [(1, "Changed")] if step == "original_down" else []
        )
    if step == "original_down":
        assert set(applied(subject.database)) == {ddl["id"], "0011_insert", "0012_update"}
    elif step in ("final_up", "last_up"):
        assert applied(subject.database) == [ddl["id"]]
    elif step == "final_down":
        assert ddl["id"] in applied(subject.database)
        assert len(applied(subject.database)) == 2
    if step == "last_mark":
        assert counters["body"] == 4
        assert ddl["id"] in applied(subject.database)
        assert len(applied(subject.database)) == 1


def assert_ordered_completion(subject, monkeypatch, bodies, groups, steps):
    """Compare real committed calls with literal SQL, rows and ordered identities."""
    entry = literal("completion_states.json")["entry"]
    assert state(subject.database) == entry
    entry_schema, entry_ids = schema(subject.database), applied(subject.database)
    table_schema = sorted(entry_schema + [[
        "table", "completion_probe", "completion_probe",
        "CREATE TABLE completion_probe(id INTEGER PRIMARY KEY,value TEXT NOT NULL)",
    ]])
    indexed_schema = sorted(table_schema + [[
        "index", "completion_value", "completion_probe",
        "CREATE INDEX completion_value ON completion_probe(value)",
    ]])
    identities = [pair(subject, body["id"], body["forward"], body["reverse"],
                       source=body["source"]) for body in bodies]
    owned(subject, migration_request(subject, "apply", identities))
    assert state(subject.database) == entry
    assert schema(subject.database) == indexed_schema
    original_files = files(subject)
    boundaries = committed_boundaries(subject, monkeypatch)

    assert finish(subject)["status"] == "completed"
    assert task_status(subject) == "completed"
    final_files = files(subject)
    ddl_files = {name: value for name, value in original_files.items() if name.startswith("schema/")}
    assert {name: value for name, value in final_files.items() if name.startswith("schema/")} == ddl_files
    data_files = {name: value for name, value in final_files.items() if name.startswith("data/")}
    assert len(data_files) == 2 * len(groups)
    resolved = {}
    final_data_ids = []
    for group in groups:
        matching = [Path(name).stem for name, value in data_files.items()
                    if not name.endswith(".rollback.sql") and value == group["forward"].encode("utf-8")]
        assert len(matching) == 1
        identity = matching[0]
        if group["id"] is None:
            assert identity not in identities
        else:
            assert identity == group["id"]
        assert data_files[f"data/{identity}.rollback.sql"] == group["reverse"].encode("utf-8")
        final_data_ids.append(identity)
        resolved[group["label"]] = identity
    assert len(set(final_data_ids)) == len(groups)
    assert set(data_files) == {
        f"data/{identity}{suffix}" for identity in final_data_ids
        for suffix in (".sql", ".rollback.sql")
    }
    assert len(boundaries) == len(steps)
    active = set(identities)
    for event, (direction, label, probe, indexed) in zip(boundaries, steps, strict=True):
        identity = resolved.get(label, label)
        assert (event["direction"], event["id"]) == (direction, identity)
        if direction == "down":
            active.remove(identity)
        else:
            active.add(identity)
        assert event["state"] == entry
        assert event["probe"] == probe
        assert event["schema"] == (entry_schema if probe is None
                                   else indexed_schema if indexed else table_schema)
        assert event["applied"] == sorted(set(entry_ids) | active)
    assert state(subject.database) == entry
    assert schema(subject.database) == indexed_schema
    assert applied(subject.database) == sorted(set(entry_ids) | active)
    assert active == {body["id"] for body in bodies if body["source"] == "schema"} | set(final_data_ids)
    with closing(sqlite3.connect(subject.database.as_uri() + "?mode=ro", uri=True)) as connection:
        assert [list(row) for row in connection.execute(
            "SELECT id,value FROM completion_probe ORDER BY id")] == steps[-1][2]


def test_schema_data_interleaving_keeps_dependency_ids_and_executes_cycle(subject, monkeypatch):
    assert_ordered_completion(subject, monkeypatch, literal("interleaving.json"), [{
        "label": "$data", "id": "0011_data",
        "forward": "-- depends: 0010_owned_schema\nINSERT INTO completion_probe(id,value) VALUES(1,'Original');\n",
        "reverse": "DELETE FROM completion_probe WHERE id=1;\n",
    }], [
        ["down", "0012_dependent_schema", [[1, "Original"]], False],
        ["down", "0011_data", [], False],
        ["down", "0010_owned_schema", None, False],
        ["up", "0010_owned_schema", [], False],
        ["up", "$data", [[1, "Original"]], False],
        ["up", "0012_dependent_schema", [[1, "Original"]], True],
        ["down", "0012_dependent_schema", [[1, "Original"]], False],
        ["down", "$data", [], False],
        ["down", "0010_owned_schema", None, False],
        ["up", "0010_owned_schema", [], False],
        ["up", "$data", [[1, "Original"]], False],
        ["up", "0012_dependent_schema", [[1, "Original"]], True],
    ])


@pytest.mark.parametrize("case", literal("order_segments.json"),
                         ids=["separated_runs", "referenced_interior"])
def test_completion_splits_dml_at_ddl_and_referenced_identities(subject, monkeypatch, case):
    assert_ordered_completion(subject, monkeypatch, case["bodies"], case["groups"], case["steps"])


def test_completion_preserves_files_outside_actual_task_sources(subject):
    owned(subject, literal("contract.json")["ordinary_create"])
    foreign = subject.directory.parent / "other-task"
    foreign.mkdir()
    forward = foreign / "9999_foreign.sql"
    reverse = foreign / "9999_foreign.rollback.sql"
    forward.write_bytes(b"SELECT 'Other Task forward';\n")
    reverse.write_bytes(b"SELECT 'Other Task reverse';\n")
    assert finish(subject)["status"] == "completed"
    assert forward.read_bytes() == b"SELECT 'Other Task forward';\n"
    assert reverse.read_bytes() == b"SELECT 'Other Task reverse';\n"
