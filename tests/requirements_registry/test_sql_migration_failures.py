"""Actual SQL-body failures are distinct from the standard library's status gap."""

from contextlib import closing
from copy import deepcopy
from pathlib import Path
import os
import sqlite3

import pytest

from poise.modules.foundation.errors import PoiseError
from requirements_registry.sql_migration_support import (
    applied, connected_to, files, literal, migration_request, owned, pair, schema, start, state,
)


@pytest.fixture
def subject(project):
    return start(project)


@pytest.mark.parametrize("kind", ["ddl", "dml", "combined"])
@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_later_sql_error_rolls_back_whole_body(subject, monkeypatch, kind, direction):
    assert owned(subject, literal("contract.json")["query"]) == literal("contract.json")["query_response"]
    case = literal("body_faults.json")[kind]
    # Prepare ordinary library bookkeeping through a real successful transition.
    preparation = pair(subject, "0007_bookkeeping",
                       "UPDATE requirements_meta SET revision=revision;\n",
                       "UPDATE requirements_meta SET revision=revision;\n")
    owned(subject, migration_request(subject, "apply", [preparation]))
    preceding = pair(subject, "0008_prior",
                     "UPDATE domain_areas SET definition='Earlier successful file' WHERE id='D1';\n",
                     "UPDATE domain_areas SET definition='Domain fixture.' WHERE id='D1';\n")
    identity = pair(subject, "0010_body_fault",
                    case["failed_forward"] if direction == "forward" else case["forward"],
                    case["failed_reverse"] if direction == "reverse" else case["reverse"],
                    source="schema" if kind == "ddl" else "data")
    identities = [preceding, identity]
    if direction == "reverse":
        following = pair(subject, "0011_later",
                         "UPDATE domain_areas SET definition='Later successful file' WHERE id='D1';\n",
                         "UPDATE domain_areas SET definition='Earlier successful file' WHERE id='D1';\n")
        identities.append(following)
        owned(subject, migration_request(subject, "apply", identities))
    before = state(subject.database)
    before_schema = schema(subject.database)
    before_ids = applied(subject.database)
    expected = deepcopy(before)
    expected["tables"]["domain_areas"] = [["D1", "Domain one", "Earlier successful file"]]
    expected_ids = ((set(before_ids) | {preceding}) if direction == "forward"
                    else (set(before_ids) - {following}))
    published = files(subject)
    traces = []
    original = sqlite3.connect

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.set_trace_callback(traces.append if connected_to(self, subject.database) else None)

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    action = "apply" if direction == "forward" else "rollback"
    with pytest.raises(PoiseError):
        owned(subject, migration_request(subject, action, identities))

    # The later constraint error was reached after at least one successful statement.
    later = "CREATE UNIQUE INDEX fault_duplicate" if kind == "ddl" else "'Duplicate','Failure'"
    assert any(later in statement for statement in traces)
    first = case[f"failed_{direction}"].splitlines()[0].rstrip(";")
    assert any(first in statement for statement in traces)
    assert state(subject.database) == expected
    assert schema(subject.database) == before_schema
    assert applied(subject.database) == sorted(expected_ids)
    assert files(subject) == published
    if kind == "ddl" and direction == "reverse":
        with closing(sqlite3.connect(subject.database)) as connection:
            assert connection.execute("SELECT id,value FROM fault_probe ORDER BY id").fetchall() == []


def test_publication_failure_cannot_commit_business_rows_or_receipt(subject, monkeypatch):
    contract = literal("contract.json")
    assert owned(subject, contract["query"]) == contract["query_response"]
    before, before_ids = state(subject.database), applied(subject.database)
    original = Path.open
    original_os_open = os.open

    def denied(path, mode="r", *args, **kwargs):
        if path.is_relative_to(subject.directory) and any(flag in mode for flag in "wax+"):
            raise PermissionError("Test-owned migration publication denied")
        return original(path, mode, *args, **kwargs)

    def denied_descriptor(path, flags, *args, **kwargs):
        if Path(path).is_relative_to(subject.directory) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise PermissionError("Test-owned migration publication denied")
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(os, "open", denied_descriptor)
    with pytest.raises(PoiseError):
        owned(subject, contract["ordinary_create"])
    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == {}


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_committed_body_with_failed_status_requires_manual_recovery(subject, monkeypatch, direction):
    contract = literal("contract.json")
    assert owned(subject, contract["query"]) == contract["query_response"]
    # A successful transition prepares standard status tables before the measured gap.
    initial_ids = set(applied(subject.database))
    owned(subject, contract["ordinary_create"])
    committed = state(subject.database)
    ids = applied(subject.database)
    task_ids = sorted(set(ids) - initial_ids)
    assert len(task_ids) == 1
    blocked = []
    from yoyo.backends.base import DatabaseBackend

    method = "mark_one" if direction == "forward" else "unmark_one"
    original_status = getattr(DatabaseBackend, method)

    def fail_status(backend, *args, **kwargs):
        if connected_to(backend.connection, subject.database):
            blocked.append(method)
            raise sqlite3.OperationalError("Test-owned library status failure")
        return original_status(backend, *args, **kwargs)

    monkeypatch.setattr(DatabaseBackend, method, fail_status)
    if direction == "forward":
        request = contract["ordinary_update"]
    else:
        request = migration_request(subject, "rollback", task_ids)
    with pytest.raises(PoiseError, match="(?i)manual|repair|recover"):
        owned(subject, request)
    assert blocked
    # SQL is already committed. This is explicitly NOT the SQL-body rollback oracle.
    after = state(subject.database)
    assert after != committed
    assert applied(subject.database) == ids
    if direction == "forward":
        assert after["revision"] == 3
        assert any(row[0] == "sql-update" for row in after["receipts"])
    else:
        assert after["revision"] == 1
        assert all(row[0] != "sql-create" for row in after["receipts"])
    # A receipt cannot silently turn unresolved status into a normal successful replay.
    with pytest.raises(PoiseError, match="(?i)manual|repair|recover"):
        owned(subject, request)
    assert state(subject.database) == after
    assert applied(subject.database) == ids
