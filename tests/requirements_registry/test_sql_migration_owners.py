"""Every accepted mutation owner uses the same Task-bound SQL boundary."""

from contextlib import closing
import sqlite3

import pytest

from requirements_registry.sql_migration_support import (
    FIXTURES, applied, cli, files, literal, migration_request, owned, schema, start, state,
)
from requirements_registry.test_sql_legacy_export import clear_target


@pytest.fixture
def subject(project):
    return start(project)


def packet(subject, operation):
    if operation == "command":
        return literal("contract.json")["ordinary_create"]
    if operation == "apply":
        return literal("apply.json")
    if operation == "import_bootstrap":
        return {"operation": operation, "input": {
            "request_id": "sql-bootstrap", "path": str(FIXTURES / "bootstrap.json"),
        }}
    assert operation == "migrate"
    return {"operation": "migrate", "input": {}}


@pytest.mark.parametrize("operation", ["apply", "command", "import_bootstrap", "migrate"])
def test_standalone_mutation_refuses_instead_of_bypassing_task(subject, operation):
    if operation == "import_bootstrap":
        clear_target(subject)
    before, before_schema = state(subject.database), schema(subject.database)

    code, response = cli(subject, packet(subject, operation))

    assert code == 2
    assert response == literal("protocol.json")["standalone_denial"]
    assert state(subject.database) == before
    assert schema(subject.database) == before_schema
    assert files(subject) == {}


def test_specialized_apply_emits_reversible_pair(subject):
    before, before_ids = state(subject.database), set(applied(subject.database))
    response = owned(subject, literal("apply.json"))
    assert response == {"status": "applied", "revision": 2, "replayed": False}
    assert ["R2", "system", "current", "Applied requirement."] in state(subject.database)["tables"]["requirements"]
    identities = sorted(set(applied(subject.database)) - before_ids)
    assert len(identities) == 1
    assert len(files(subject)) == 2
    owned(subject, migration_request(subject, "rollback", identities))
    assert state(subject.database) == before


def test_bootstrap_emits_one_literal_data_pair_with_inverse(subject):
    clear_target(subject)
    before, before_ids = state(subject.database), set(applied(subject.database))
    response = owned(subject, packet(subject, "import_bootstrap"))
    assert response == {"status": "imported", "revision": 1, "replayed": False}
    after = state(subject.database)
    assert after["tables"]["requirements"] == [
        ["BA", "application", "current", "Bootstrap application."],
        ["BS", "system", "current", "Bootstrap system."],
    ]
    assert after["tables"]["requirement_links"] == [["BS", "BA"]]
    identities = sorted(set(applied(subject.database)) - before_ids)
    assert len(identities) == 1
    assert len(files(subject)) == 2
    owned(subject, migration_request(subject, "rollback", identities))
    assert state(subject.database) == before


def test_historical_migrate_emits_schema_pair_and_restores_version_one(subject):
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.executescript((FIXTURES / "historical_v1.sql").read_text(encoding="utf-8"))
    before, before_schema = state(subject.database), schema(subject.database)
    before_ids = set(applied(subject.database))

    assert owned(subject, packet(subject, "migrate")) == {
        "status": "migrated", "from_version": 1, "to_version": 2, "changed": True,
    }

    assert state(subject.database)["schema_version"] == 2
    identities = sorted(set(applied(subject.database)) - before_ids)
    assert len(identities) == 1
    assert len(files(subject)) == 2
    owned(subject, migration_request(subject, "rollback", identities))
    assert state(subject.database) == before
    assert schema(subject.database) == before_schema
