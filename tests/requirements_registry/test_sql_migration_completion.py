"""Task completion must execute SQL; migration marks cannot stand in for bodies."""

from contextlib import closing
import sqlite3

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


def test_completion_with_zero_migrations_keeps_requirements_state(subject):
    before = state(subject.database)
    finish(subject)
    assert task_status(subject) == "completed"
    assert state(subject.database) == before
    assert files(subject) == {}


def test_completion_really_reverses_original_set_and_replays_final_set(subject, monkeypatch):
    contract = literal("completion.json")
    ddl = contract["schema"]
    identities = [pair(subject, ddl["id"], ddl["forward"], ddl["reverse"], source="schema")]
    for body in contract["data"]:
        identities.append(pair(subject, body["id"], body["forward"], body["reverse"]))
    owned(subject, migration_request(subject, "apply", identities))
    before = state(subject.database)
    before_schema = schema(subject.database)
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

    finish(subject)

    assert task_status(subject) == "completed"
    assert statements == contract["expected_sql"]
    assert state(subject.database) == before
    assert schema(subject.database) == before_schema
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


def test_completion_with_one_data_pair_executes_down_up_down_up(subject, monkeypatch):
    owned(subject, literal("contract.json")["ordinary_create"])
    before = state(subject.database)
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
    assert finish(subject)["status"] == "completed"
    assert statements == ["delete", "insert", "delete", "insert"]
    assert state(subject.database) == before
    assert len(files(subject)) == 2


@pytest.mark.parametrize("step", ["original_down", "final_up", "final_down", "last_up", "last_mark"])
def test_completion_failure_never_publishes_task_complete(subject, monkeypatch, step):
    contract = literal("completion.json")
    ddl = contract["schema"]
    identities = [pair(subject, ddl["id"], ddl["forward"], ddl["reverse"], source="schema")]
    for body in contract["data"]:
        identities.append(pair(subject, body["id"], body["forward"], body["reverse"]))
    owned(subject, migration_request(subject, "apply", identities))
    before = state(subject.database)
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
    assert state(subject.database) == before
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


def test_unsupported_schema_data_interleaving_refuses_before_reversal(subject, monkeypatch):
    identities = []
    for body in literal("interleaving.json"):
        identities.append(pair(subject, body["id"], body["forward"], body["reverse"], source=body["source"]))
    owned(subject, migration_request(subject, "apply", identities))
    before, before_ids, before_files = state(subject.database), applied(subject.database), files(subject)
    destructive = []
    original = sqlite3.connect

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_trace_callback(self.observe)

        def observe(self, statement):
            lower = statement.strip().lower()
            if self.target and "completion_probe" in lower and lower.startswith(("insert ", "update ", "delete ", "drop ", "create ")):
                destructive.append(statement)

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(PoiseError, match="(?i)depend|interleav|order|unsupported"):
        finish(subject)
    assert destructive == []
    assert task_status(subject) != "completed"
    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == before_files


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
