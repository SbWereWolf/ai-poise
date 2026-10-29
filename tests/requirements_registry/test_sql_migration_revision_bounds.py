"""SQLite signed INTEGER boundaries must survive literal capture and inverse SQL."""

from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from poise.modules.foundation.errors import PoiseError
from requirements_registry.sql_migration_support import (
    applied, literal, migration_request, owned, start, state,
)
from requirements_registry.test_sql_legacy_export import clear_target, legacy, request


@pytest.fixture
def subject(project):
    return start(project)


def test_last_integer_revision_transition_and_next_refusal_are_truthful(subject):
    contract = literal("contract.json")
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.execute("UPDATE requirements_meta SET revision=9223372036854775806")
        connection.commit()
    packet = deepcopy(contract["ordinary_create"])
    packet["input"]["expected_revision"] = 9223372036854775806

    assert owned(subject, packet) == {
        "status": "applied", "revision": 9223372036854775807, "replayed": False,
        "results": [{"id": "create", "affected_rows": 1}],
    }
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT revision,typeof(revision) FROM requirements_meta").fetchall() == [(9223372036854775807, "integer")]
    before, before_ids = state(subject.database), applied(subject.database)
    packet = deepcopy(contract["ordinary_update"])
    packet["input"]["expected_revision"] = 9223372036854775807

    with pytest.raises(PoiseError):
        owned(subject, packet)

    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT revision,typeof(revision) FROM requirements_meta").fetchall() == [(9223372036854775807, "integer")]


@pytest.mark.parametrize("revision", [0, 9223372036854775807])
def test_full_capture_and_inverse_preserve_exact_integer_revision(subject, revision):
    source = legacy(subject)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE requirements_meta SET revision=?", (revision,))
        if revision == 0:
            connection.execute("DELETE FROM requirement_requests")
        connection.commit()
    captured = state(source)
    clear_target(subject)
    before = state(subject.database)

    exported = owned(subject, request(subject, source, "full", []))
    owned(subject, migration_request(subject, "apply", exported["migration_ids"]))

    assert state(subject.database) == captured
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT revision,typeof(revision) FROM requirements_meta").fetchall() == [(revision, "integer")]
    owned(subject, migration_request(subject, "rollback", exported["migration_ids"]))
    assert state(subject.database) == before


def test_selected_source_max_does_not_replace_target_revision_authority(subject):
    source = legacy(subject)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE requirements_meta SET revision=9223372036854775807")
        # Same domain row as the target; this scenario isolates revision authority.
        connection.execute("UPDATE domain_areas SET definition=? WHERE id=?", ("Domain fixture.", "D1"))
        connection.commit()
    exported = owned(subject, request(subject, source, "selected", ["A1"]))
    owned(subject, migration_request(subject, "apply", exported["migration_ids"]))
    assert state(subject.database)["revision"] == 2
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT typeof(revision) FROM requirements_meta").fetchone() == ("integer",)


def test_sql_above_max_is_real_while_bound_python_integer_is_rejected(tmp_path):
    with closing(sqlite3.connect(tmp_path / "integer.sqlite")) as connection:
        assert connection.execute("SELECT typeof(9223372036854775808)").fetchone() == ("real",)
        with pytest.raises(OverflowError):
            connection.execute("SELECT typeof(?)", (9223372036854775808,))
