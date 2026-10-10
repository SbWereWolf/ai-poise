"""Integration subject: RequirementsCommands/Store with independent SQLite observers."""
from pathlib import Path
import sqlite3

import pytest

from poise.application.requirements_registry import RequirementsCommands
from poise.infrastructure.requirements_registry import RequirementsStore
from poise.modules.foundation.errors import DomainError, VersionConflict


def put(identifier, text, level="system"):
    return {"kind": "put_requirement", "requirement": {
        "id": identifier, "level": level, "status": "current", "text": text,
    }}


def packet(request_id, revision, operations):
    return {"request_id": request_id, "expected_revision": revision, "operations": operations}


@pytest.fixture
def observed(tmp_path):
    database = tmp_path / "requirements.sqlite"
    commands = RequirementsCommands(
        RequirementsStore(database, tmp_path / "requirements.lock", 1, 0.01), 20,
    )
    seed = packet("seed", 0, [
        put("SYS", "Сохранить требования."),
        put("APP", "Хранить реестр.", "application"),
        {"kind": "link", "system": "SYS", "application": "APP"},
    ])
    assert commands.apply(seed) == {"revision": 1, "replayed": False}
    with sqlite3.connect(database) as connection:
        connection.executescript(
            (Path(__file__).parent / "fixtures/write_observation.sql").read_text()
        )
    return database, commands, seed


def snapshot(database):
    with sqlite3.connect(database) as connection:
        return (
            connection.execute(
                "SELECT rowid,id,level,status,text FROM requirements ORDER BY id"
            ).fetchall(),
            connection.execute(
                "SELECT rowid,system_id,application_id FROM requirement_links "
                "ORDER BY system_id,application_id"
            ).fetchall(),
            connection.execute("SELECT revision FROM requirements_meta").fetchall(),
            connection.execute(
                "SELECT request_id,digest,response FROM requirement_requests ORDER BY request_id"
            ).fetchall(),
            connection.execute("SELECT kind,identity FROM observed_writes ORDER BY rowid").fetchall(),
        )


def test_add_requirement_only_inserts_the_new_row_and_replay_is_write_free(observed):
    database, commands, seed = observed
    before = snapshot(database)
    request = packet("add", 1, [put("NEW", "Добавить требование.")])
    assert commands.apply(request) == {"revision": 2, "replayed": False}
    after = snapshot(database)
    assert after[4] == [("requirement_insert", "NEW")]
    assert after[2] == [(2,)]
    assert len(after[3]) == 2
    assert [row for row in after[0] if row[1] != "NEW"] == before[0]
    assert after[1] == before[1]
    assert [row for row in after[3] if row[0] == "seed"] == before[3]
    assert commands.apply(request) == {"revision": 2, "replayed": True}
    assert commands.apply(seed) == {"revision": 1, "replayed": True}
    assert snapshot(database) == after


def test_update_changes_only_existing_row_without_replacement(observed):
    database, commands, _ = observed
    before = snapshot(database)
    request = packet("edit", 1, [put("SYS", "Обновить формулировку.")])
    assert commands.apply(request) == {"revision": 2, "replayed": False}
    after = snapshot(database)
    assert after[4] == [("requirement_update", "SYS")]
    assert after[0] == [before[0][0], (
        before[0][1][0], "SYS", "system", "current", "Обновить формулировку.",
    )]
    assert after[1] == before[1]


def test_add_link_does_not_rewrite_old_requirements_or_links(observed):
    database, commands, _ = observed
    request = packet("link", 1, [
        put("OTHER", "Второе приложение.", "application"),
        {"kind": "link", "system": "SYS", "application": "OTHER"},
    ])
    assert commands.apply(request) == {"revision": 2, "replayed": False}
    assert snapshot(database)[4] == [
        ("requirement_insert", "OTHER"), ("link_insert", "SYS:OTHER"),
    ]


def test_unchanged_batch_records_receipt_without_rewriting_subject_rows(observed):
    database, commands, seed = observed
    before = snapshot(database)
    request = packet("same-values", 1, seed["operations"])
    assert commands.apply(request) == {"revision": 2, "replayed": False}
    after = snapshot(database)
    assert after[0:2] == before[0:2]
    assert after[4] == []
    assert after[2] == [(2,)]
    assert len(after[3]) == 2


def test_invalid_batch_has_no_partial_rows_revision_or_receipt(observed):
    database, commands, _ = observed
    before = snapshot(database)
    request = packet("invalid", 1, [
        put("NEW", "Добавить требование."),
        {"kind": "link", "system": "MISSING", "application": "APP"},
    ])
    with pytest.raises(DomainError, match="unknown system requirement"):
        commands.apply(request)
    assert snapshot(database) == before


def test_stale_revision_preserves_rows_and_receipts(observed):
    database, commands, _ = observed
    before = snapshot(database)
    with pytest.raises(VersionConflict, match="revision changed"):
        commands.apply(packet("stale", 0, [put("NEW", "Добавить требование.")]))
    assert snapshot(database) == before


def test_same_identity_cannot_change_requirement_level(observed):
    database, commands, _ = observed
    before = snapshot(database)
    with pytest.raises(DomainError, match="level is immutable"):
        commands.apply(packet("change-level", 1, [put("SYS", "Новый текст.", "application")]))
    assert snapshot(database) == before


def test_sql_failure_rolls_back_earlier_rows_revision_receipts_and_observers(observed):
    database, commands, _ = observed
    with sqlite3.connect(database) as connection:
        connection.executescript(
            (Path(__file__).parent / "fixtures/reject_new_link.sql").read_text()
        )
    before = snapshot(database)
    request = packet("sql-failure", 1, [
        put("SYS", "Обновить формулировку."),
        put("OTHER", "Второе приложение.", "application"),
        {"kind": "link", "system": "SYS", "application": "OTHER"},
    ])
    with pytest.raises(sqlite3.IntegrityError, match="test link refusal"):
        commands.apply(request)
    assert snapshot(database) == before


def test_observer_distinguishes_replace_from_update_even_without_recursive_triggers(observed):
    database, _, _ = observed
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute(
            "INSERT OR REPLACE INTO requirements(id,level,status,text) VALUES(?,?,?,?)",
            ("SYS", "system", "current", "Обновить формулировку."),
        )
    assert snapshot(database)[4] == [("requirement_insert", "SYS")]


def test_conflicting_request_id_preserves_rows_and_receipts(observed):
    database, commands, _ = observed
    before = snapshot(database)
    with pytest.raises(DomainError, match="used for another request"):
        commands.apply(packet("seed", 1, [put("NEW", "Добавить требование.")]))
    assert snapshot(database) == before
