from __future__ import annotations

from datetime import datetime, timezone

import pytest

from .helpers import (
    FIXED_NOW,
    backup_commands,
    backup_directory,
    create_database,
    expected_backup_name,
    integrity,
    live_database,
    marker,
)


def test_list_returns_only_sorted_names_and_filesystem_ctime(project):
    directory = backup_directory(project)
    directory.mkdir(parents=True)
    second = directory / "z.sqlite"
    first = directory / "a.sqlite"
    create_database(second, "second")
    create_database(first, "first")
    (directory / "ignored-directory").mkdir()
    (directory / "ignored-link").symlink_to(first)
    expected = {
        path.name: datetime.fromtimestamp(path.stat().st_ctime, timezone.utc).isoformat()
        for path in (first, second)
    }

    result = backup_commands(project).list()

    assert result == [
        {"name": "a.sqlite", "created_at": expected["a.sqlite"]},
        {"name": "z.sqlite", "created_at": expected["z.sqlite"]},
    ]
    assert all(set(item) == {"name", "created_at"} for item in result)


def test_create_uses_configured_database_and_reports_verified_sizes(project):
    database = live_database(project)
    create_database(database, "live-marker")
    task_sentinel = database.parent / "task" / "T1" / "artifact.txt"
    sprint_sentinel = database.parent / "sprint" / "S1" / "artifact.txt"
    for sentinel in (task_sentinel, sprint_sentinel):
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("unchanged", encoding="utf-8")
    entries_before = {path.name for path in database.parent.iterdir()}

    result = backup_commands(project).create()
    backup = backup_directory(project) / result["name"]

    assert result == {
        "success": True,
        "name": expected_backup_name(database),
        "live_size_bytes": database.stat().st_size,
        "backup_size_bytes": backup.stat().st_size,
    }
    assert integrity(backup) == "ok"
    assert marker(backup) == "live-marker"
    assert task_sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sprint_sentinel.read_text(encoding="utf-8") == "unchanged"
    assert {path.name for path in database.parent.iterdir()} == entries_before | {"backups"}
    assert sorted(path.name for path in backup_directory(project).iterdir()) == [result["name"]]


def test_create_rejects_non_database_without_success_or_leftover_copy(project):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a sqlite database")

    with pytest.raises(PoiseError, match="integrity|database|SQLite"):
        backup_commands(project).create()

    directory = backup_directory(project)
    assert not directory.exists() or list(directory.iterdir()) == []


def test_create_rejects_existing_generated_name_without_overwrite(project):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    create_database(database, "live")
    destination = backup_directory(project) / expected_backup_name(database)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")
    before = destination.read_bytes()

    with pytest.raises(PoiseError, match="exists|collision"):
        backup_commands(project).create()

    assert destination.read_bytes() == before
    assert marker(database) == "live"


def test_restore_exact_valid_name_atomically_replaces_live_database(project):
    database = live_database(project)
    create_database(database, "old-live")
    mode = database.stat().st_mode & 0o777
    selected = backup_directory(project) / "selected.sqlite"
    create_database(selected, "restored")

    result = backup_commands(project).restore(selected.name)

    assert result == {"success": True, "name": selected.name}
    assert marker(database) == "restored"
    assert integrity(database) == "ok"
    assert database.stat().st_mode & 0o777 == mode
    assert not list(database.parent.glob(f".{database.name}.restore-*.pending"))


@pytest.mark.parametrize(
    ("name", "prepare", "error"),
    [
        ("missing.sqlite", lambda path: None, "missing|not found"),
        ("corrupt.sqlite", lambda path: path.write_bytes(b"corrupt"), "integrity|database|SQLite"),
    ],
)
def test_restore_invalid_copy_keeps_live_database_byte_identical(project, name, prepare, error):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    create_database(database, "live")
    before = database.read_bytes()
    candidate = backup_directory(project) / name
    candidate.parent.mkdir(parents=True)
    prepare(candidate)

    with pytest.raises(PoiseError, match=error):
        backup_commands(project).restore(name)

    assert database.read_bytes() == before
    assert marker(database) == "live"
    assert not list(database.parent.glob(f".{database.name}.restore-*.pending"))


def test_restore_unreadable_copy_keeps_live_database_unchanged(project):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    create_database(database, "live")
    before = database.read_bytes()
    candidate = backup_directory(project) / "unreadable.sqlite"
    create_database(candidate, "backup")
    candidate.chmod(0)
    try:
        with pytest.raises(PoiseError, match="read|permission|open"):
            backup_commands(project).restore(candidate.name)
    finally:
        candidate.chmod(0o600)

    assert database.read_bytes() == before
    assert marker(database) == "live"


@pytest.mark.parametrize("name", ["", ".", "..", "../escape.sqlite", "a/b.sqlite", "a\\b.sqlite"])
def test_restore_rejects_non_basename_and_dot_components(project, name):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    create_database(database, "live")
    before = database.read_bytes()

    with pytest.raises(PoiseError, match="name|component|backup"):
        backup_commands(project).restore(name)

    assert database.read_bytes() == before


def test_restore_rejects_symlink(project):
    try:
        from poise.common import PoiseError
    except ModuleNotFoundError as exc:
        pytest.fail(f"public poise error contract is absent: {exc}")
    database = live_database(project)
    create_database(database, "live")
    outside = database.parent / "outside.sqlite"
    create_database(outside, "outside")
    link = backup_directory(project) / "linked.sqlite"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    before = database.read_bytes()

    with pytest.raises(PoiseError, match="symlink|regular|backup"):
        backup_commands(project).restore(link.name)

    assert database.read_bytes() == before
    assert marker(outside) == "outside"
