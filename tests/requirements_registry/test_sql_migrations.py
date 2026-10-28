"""Literal SQL migration contracts through real Requirements/Task boundaries."""

from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from requirements_registry.sql_migration_support import (
    applied, catalog, cli, connected_to, files, literal, migration_request, owned, schema, start, state,
)


@pytest.fixture
def subject(project):
    return start(project)


def test_read_query_preserves_registry_and_creates_no_migration(subject):
    contract = literal("contract.json")
    before = state(subject.database)
    before_files = files(subject)
    before_catalog = catalog(subject.database)

    code, response = cli(subject, contract["query"])

    assert code == 0
    assert response == contract["query_response"]
    assert state(subject.database) == before
    assert files(subject) == before_files == {}
    assert catalog(subject.database) == before_catalog


def test_owned_mutation_publishes_pair_and_preserves_interior_nul(subject):
    contract = literal("contract.json")
    before_applied = applied(subject.database)
    before_schema = schema(subject.database)

    response = owned(subject, contract["ordinary_create"])

    assert response == contract["ordinary_create_response"]
    emitted = files(subject)
    assert len(emitted) == 2
    assert len([name for name in emitted if name.endswith(".rollback.sql")]) == 1
    assert all(b"\x00" not in body for body in emitted.values())
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute(
            "SELECT definition,typeof(definition),hex(definition) "
            "FROM domain_areas WHERE id=?", ("D2",),
        ).fetchone() == ("A\x00B", "text", "410042")
        receipts = connection.execute(
            "SELECT request_id,digest,response FROM requirement_requests WHERE request_id=?",
            ("sql-create",),
        ).fetchall()
    assert [list(row) for row in receipts] == literal("ordinary_create_receipt.json")
    assert schema(subject.database) == before_schema
    new_ids = set(applied(subject.database)) - set(before_applied)
    # Ordinary library bookkeeping, not a counter pretending SQL was applied.
    assert len(new_ids) >= 1
    assert any(name.endswith(f"/{identity}.sql") for identity in new_ids for name in emitted)


@pytest.mark.parametrize("table", literal("family_contracts.json"), ids=str)
def test_each_table_has_real_forward_inverse_and_reapply(subject, table):
    contracts = literal("family_contracts.json")[table]
    rows = literal("family_rows.json")[table]
    for action, case in contracts.items():
        before = state(subject.database)
        before_schema = schema(subject.database)
        before_ids = set(applied(subject.database))
        before_files = files(subject)
        expected = deepcopy(before)
        expected["tables"][table] = rows[action]
        expected["revision"] = case["response"]["revision"]
        expected["receipts"] = sorted([*before["receipts"], case["receipt"]])

        assert owned(subject, case["packet"]) == case["response"]

        assert state(subject.database) == expected
        assert schema(subject.database) == before_schema
        identities = sorted(set(applied(subject.database)) - before_ids)
        assert len(identities) == 1
        assert len(files(subject)) == len(before_files) + 2
        assert owned(subject, migration_request(subject, "rollback", identities)) == {
            "status": "rolled_back", "migration_ids": identities,
        }
        assert state(subject.database) == before
        assert schema(subject.database) == before_schema
        assert set(applied(subject.database)) == before_ids
        assert owned(subject, migration_request(subject, "apply", identities)) == {
            "status": "applied", "migration_ids": identities,
        }
        assert state(subject.database) == expected
        assert schema(subject.database) == before_schema


def test_successful_request_replay_reuses_pair_and_receipt(subject):
    contract = literal("contract.json")
    assert owned(subject, contract["ordinary_create"]) == contract["ordinary_create_response"]
    committed = state(subject.database)
    committed_files = files(subject)
    committed_ids = applied(subject.database)

    response = owned(subject, contract["ordinary_create"])

    assert response == {"status": "applied", "revision": 2, "replayed": True,
                        "results": [{"id": "create", "affected_rows": 1}]}
    assert state(subject.database) == committed
    assert files(subject) == committed_files
    assert applied(subject.database) == committed_ids


def test_no_op_still_has_reversible_revision_and_receipt(subject):
    contract = literal("contract.json")
    before = state(subject.database)
    previous_ids = set(applied(subject.database))

    assert owned(subject, contract["no_op"]) == contract["no_op_response"]

    after = state(subject.database)
    assert after["tables"] == before["tables"]
    assert after["revision"] == 2
    assert len(after["receipts"]) == len(before["receipts"]) + 1
    assert len(files(subject)) == 2
    ids = sorted(set(applied(subject.database)) - previous_ids)
    assert len(ids) == 1
    owned(subject, migration_request(subject, "rollback", ids))
    assert state(subject.database) == before


def test_pair_exists_before_first_business_write(subject, monkeypatch):
    contract = literal("contract.json")
    original = sqlite3.connect
    observed = []

    class Observed(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.target = connected_to(self, subject.database)
            self.set_trace_callback(self.inspect_statement)

        def inspect_statement(self, statement):
            if self.target and statement.lstrip().upper().startswith(("INSERT INTO DOMAIN_AREAS", 'INSERT INTO "DOMAIN_AREAS"')):
                emitted = files(subject)
                observed.append((len(emitted), sum(name.endswith(".rollback.sql") for name in emitted)))

    def connect(*args, **kwargs):
        kwargs["factory"] = Observed
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert owned(subject, contract["ordinary_create"]) == contract["ordinary_create_response"]
    assert observed
    assert all(value == (2, 1) for value in observed)


def test_update_and_inverse_preserve_nul_and_sql_looking_text(subject):
    contract = literal("contract.json")
    owned(subject, contract["ordinary_create"])
    before = state(subject.database)
    before_ids = set(applied(subject.database))

    assert owned(subject, contract["ordinary_update"]) == contract["ordinary_update_response"]

    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute(
            "SELECT definition,typeof(definition) FROM domain_areas WHERE id=?", ("D2",),
        ).fetchone() == ("x'; DROP TABLE domain_areas; --\nΩ end", "text")
    ids = sorted(set(applied(subject.database)) - before_ids)
    assert len(ids) == 1
    owned(subject, migration_request(subject, "rollback", ids))
    assert state(subject.database) == before
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute(
            "SELECT typeof(definition),hex(definition) FROM domain_areas WHERE id=?", ("D2",),
        ).fetchone() == ("text", "410042")
