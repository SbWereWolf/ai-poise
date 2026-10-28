"""Captured legacy snapshots produce standalone literal SQL, never live-source reads."""

from contextlib import closing
import sqlite3
import shutil

import pytest

from poise.modules.foundation.errors import PoiseError
from requirements_registry.sql_migration_support import (
    FIXTURES, applied, catalog, files, literal, migration_request, owned, start, state,
)


@pytest.fixture
def subject(project):
    return start(project)


def legacy(subject):
    source = subject.project["root"] / "legacy-requirements.sqlite"
    with closing(sqlite3.connect(subject.database)) as original:
        with closing(sqlite3.connect(source)) as destination:
            original.backup(destination)
            destination.executescript((FIXTURES / "legacy.sql").read_text(encoding="utf-8"))
    return source


def request(subject, source, mode, identities):
    return {"operation": "export", "input": {
        "request_id": "legacy-export", "source": str(source), "target": str(subject.database),
        "mode": mode, "requirement_ids": identities, "output_directory": str(subject.directory),
    }}


def clear_target(subject):
    # This database belongs exclusively to the test. No operator database is opened.
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.executescript((FIXTURES / "empty_target.sql").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source_after_capture", ["missing", "changed"])
def test_full_export_is_committed_wal_snapshot_with_original_history(subject, source_after_capture):
    source = legacy(subject)
    clear_target(subject)
    before = state(subject.database)
    original_ids = set(applied(subject.database))
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE requirements SET text=? WHERE id=?", ("Committed WAL value", "S1"))
        writer.commit()
        captured = state(source)
        main_only = subject.project["root"] / "main-file-only.sqlite"
        shutil.copyfile(source, main_only)
        # A main-file copy is independently shown to omit the committed WAL frame.
        assert state(main_only) != captured
        assert ["S1", "system", "current", "Committed WAL value"] in captured["tables"]["requirements"]
        assert ["S1", "system", "current", "System one."] in state(main_only)["tables"]["requirements"]
        writer.execute("UPDATE requirements SET text=? WHERE id=?", ("Uncommitted", "S1"))

        response = owned(subject, request(subject, source, "full", []))

        assert response["status"] == "generated"
        assert len(response["migration_ids"]) == 1
        assert state(subject.database) == before
        assert len(files(subject)) == 2
        writer.rollback()
    assert source.is_file()
    # Applying literal output must not consult the original database again.
    hidden = source.with_suffix(".captured")
    if source_after_capture == "missing":
        source.rename(hidden)
    else:
        with closing(sqlite3.connect(source)) as connection:
            connection.execute("UPDATE requirements SET text=? WHERE id=?", ("Changed after capture", "S1"))
            connection.commit()
    owned(subject, migration_request(subject, "apply", response["migration_ids"]))
    assert state(subject.database) == captured
    assert set(applied(subject.database)) - original_ids == set(response["migration_ids"])
    owned(subject, migration_request(subject, "rollback", response["migration_ids"]))
    assert state(subject.database) == before
    if source_after_capture == "missing":
        hidden.rename(source)
    assert source.is_file()


def test_selected_export_closes_dependencies_but_retains_target_history(subject):
    source = legacy(subject)
    with closing(sqlite3.connect(source)) as connection:
        connection.executescript((FIXTURES / "selected_closure.sql").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.executescript((FIXTURES / "missing_dependencies.sql").read_text(encoding="utf-8"))
    before = state(subject.database)
    response = owned(subject, request(subject, source, "selected", ["A1"]))
    assert response["status"] == "generated"
    assert state(subject.database) == before

    owned(subject, migration_request(subject, "apply", response["migration_ids"]))

    after = state(subject.database)
    assert after["revision"] == 2
    assert before["tables"] != literal("selected_business.json")
    assert after["tables"] == literal("selected_business.json")
    assert ["S1", "A1"] in after["tables"]["requirement_links"]
    assert after["tables"]["requirement_entities"] == [["A1", "D1", "E1"]]
    assert after["tables"]["requirement_actions"] == [["A1", "D1", "X1"]]
    assert all(row in after["receipts"] for row in before["receipts"])
    assert len(after["receipts"]) == len(before["receipts"]) + 1
    assert any(row[0] == "legacy-export" for row in after["receipts"])
    assert all(row[0] != "source-only" for row in after["receipts"])
    with closing(sqlite3.connect(subject.database)) as connection:
        assert connection.execute("SELECT typeof(definition),hex(definition) FROM domain_areas WHERE id='D1'").fetchone() == ("text", "410042")
    owned(subject, migration_request(subject, "rollback", response["migration_ids"]))
    assert state(subject.database) == before
    assert source.is_file()


def test_changed_target_after_generation_refuses_without_adapting_sql(subject):
    source = legacy(subject)
    clear_target(subject)
    response = owned(subject, request(subject, source, "full", []))
    published = files(subject)
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.execute("INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)",
                           ("OTHER", "system", "current", "Concurrent target"))
        connection.commit()
    before, before_ids = state(subject.database), applied(subject.database)

    with pytest.raises(PoiseError):
        owned(subject, migration_request(subject, "apply", response["migration_ids"]))

    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == published
    assert source.is_file()


@pytest.mark.parametrize("bad_value", [None, 1.25, b"raw"])
def test_invalid_captured_text_type_refuses_without_coercion(subject, bad_value):
    source = legacy(subject)
    assert owned(subject, literal("contract.json")["query"]) == literal("contract.json")["query_response"]
    clear_target(subject)
    # Nullable SQLite TEXT PRIMARY KEY: real schema permits NULL here, public IDs do not.
    with closing(sqlite3.connect(source)) as connection:
        if bad_value is None:
            connection.execute("INSERT INTO requirements(id,level,status,text) VALUES(NULL,?,?,?)",
                               ("system", "current", "Invalid identifier"))
        else:
            # BLOB stays BLOB under TEXT affinity; REAL stays REAL in the INTEGER revision field.
            if isinstance(bad_value, bytes):
                connection.execute("UPDATE requirements SET text=? WHERE id=?", (bad_value, "S1"))
            else:
                connection.execute("UPDATE requirements_meta SET revision=?", (bad_value,))
        connection.commit()
    before, original_files = state(subject.database), files(subject)
    with pytest.raises(PoiseError, match="(?i)type|identifier|revision|schema|text"):
        owned(subject, request(subject, source, "full", []))
    assert state(subject.database) == before
    assert files(subject) == original_files
    assert source.is_file()


def test_identical_generation_reuses_exact_pair_and_changed_intent_refuses(subject):
    source = legacy(subject)
    clear_target(subject)
    packet = request(subject, source, "full", [])
    first = owned(subject, packet)
    assert first["status"] == "generated"
    published, before = files(subject), state(subject.database)
    assert owned(subject, packet) == first
    assert files(subject) == published
    assert state(subject.database) == before
    conflicting = request(subject, source, "selected", ["A1"])
    with pytest.raises(PoiseError, match="(?i)conflict|request|intent|collision"):
        owned(subject, conflicting)
    assert files(subject) == published
    assert state(subject.database) == before


def test_identical_selected_state_emits_no_empty_migration_or_import_receipt(subject):
    source = legacy(subject)
    with closing(sqlite3.connect(subject.database)) as connection:
        connection.executescript((FIXTURES / "legacy.sql").read_text(encoding="utf-8"))
    before = state(subject.database)

    assert owned(subject, request(subject, source, "selected", ["A1"])) == {
        "status": "unchanged", "migration_ids": [],
    }

    assert state(subject.database) == before
    assert files(subject) == {}
    assert source.is_file()


@pytest.mark.parametrize("problem", ["conflicting_target", "missing_dependency", "unsupported_schema"])
def test_invalid_transfer_refuses_without_partial_output_or_target_write(subject, problem):
    source = legacy(subject)
    assert owned(subject, literal("contract.json")["query"]) == literal("contract.json")["query_response"]
    if problem == "conflicting_target":
        # D1 is the same identity but has different literal TEXT, never silently merged.
        packet = request(subject, source, "selected", ["A1"])
    else:
        clear_target(subject)
        with closing(sqlite3.connect(source)) as connection:
            if problem == "missing_dependency":
                connection.execute("DELETE FROM domain_entities WHERE id='E1'")
            else:
                connection.execute("PRAGMA user_version=99")
            connection.commit()
        packet = request(subject, source, "full", [])
    before = state(subject.database)
    with pytest.raises(PoiseError):
        owned(subject, packet)
    assert state(subject.database) == before
    assert files(subject) == {}
    assert source.is_file()


@pytest.mark.parametrize("collision", ["forward", "reverse", "partial"])
def test_changed_or_partial_export_pair_is_not_overwritten_or_healed(subject, collision):
    source = legacy(subject)
    clear_target(subject)
    packet = request(subject, source, "full", [])
    result = owned(subject, packet)
    identity, = result["migration_ids"]
    path = subject.directory / (f"{identity}.sql" if collision == "forward" else f"{identity}.rollback.sql")
    if collision == "partial":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"SELECT 1;\n")
    before, published = state(subject.database), files(subject)
    with pytest.raises(PoiseError):
        owned(subject, packet)
    assert state(subject.database) == before
    assert files(subject) == published


def test_nullable_sqlite_receipt_primary_key_is_not_a_valid_public_request_id(subject):
    source = legacy(subject)
    assert owned(subject, literal("contract.json")["query"]) == literal("contract.json")["query_response"]
    clear_target(subject)
    with closing(sqlite3.connect(source)) as connection:
        row = connection.execute(
            "SELECT digest,response FROM requirement_requests ORDER BY request_id LIMIT 1",
        ).fetchone()
        connection.execute("INSERT INTO requirement_requests(request_id,digest,response) VALUES(NULL,?,?)", row)
        connection.commit()
        assert connection.execute("SELECT typeof(request_id) FROM requirement_requests WHERE request_id IS NULL").fetchone() == ("null",)
    before = state(subject.database)
    with pytest.raises(PoiseError, match="(?i)request|identifier|null|type"):
        owned(subject, request(subject, source, "full", []))
    assert state(subject.database) == before
    assert files(subject) == {}


def test_alternate_comparison_is_read_only_and_cannot_redirect_actual_apply(subject):
    source = legacy(subject)
    comparison = subject.project["root"] / "comparison.sqlite"
    with closing(sqlite3.connect(comparison)) as connection:
        connection.executescript((FIXTURES.parent / "sql_legacy_schema.sql").read_text(encoding="utf-8"))
    databases = (source, comparison, subject.database)
    before = {path: state(path) for path in databases}
    before_catalog = {path: catalog(path) for path in databases}
    packet = request(subject, source, "full", [])
    packet["input"]["target"] = str(comparison)

    response = owned(subject, packet)

    assert response["status"] == "generated"
    assert len(response["migration_ids"]) == 1
    assert {path: state(path) for path in databases} == before
    assert {path: catalog(path) for path in databases} == before_catalog
    published = files(subject)
    # The configured store differs from the compared empty snapshot; never redirect.
    with pytest.raises(PoiseError):
        owned(subject, migration_request(subject, "apply", response["migration_ids"]))
    assert {path: state(path) for path in databases} == before
    assert catalog(comparison) == before_catalog[comparison]
    assert catalog(source) == before_catalog[source]
    assert files(subject) == published
