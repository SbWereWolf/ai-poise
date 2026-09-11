from __future__ import annotations

import json

from .helpers import create_database, json_output, live_database, marker, run_cli


def test_backup_help_describes_list_create_restore_and_quiescence():
    result = run_cli("backup", "help")
    assert result.returncode == 0, result.stderr
    text = result.stdout.lower()
    assert all(word in text for word in ("list", "create", "restore"))
    assert "агент" in text
    assert "процесс" in text


def test_list_returns_empty_collection_when_sibling_directory_is_absent(project):
    database = live_database(project)
    create_database(database, "live")

    result = json_output(run_cli("backup", "list", "--config", str(project["config_path"])))

    assert result == {"backups": []}
    assert not (database.parent / "backups").exists()


def test_cli_create_and_restore_have_bounded_json_shapes(project):
    database = live_database(project)
    create_database(database, "before")

    created = json_output(run_cli("backup", "create", "--config", str(project["config_path"])))
    assert set(created) == {"success", "name", "live_size_bytes", "backup_size_bytes"}
    assert created["success"] is True

    database.unlink()
    create_database(database, "replacement")
    restored = json_output(
        run_cli(
            "backup",
            "restore",
            "--config",
            str(project["config_path"]),
            created["name"],
        )
    )
    assert restored == {"success": True, "name": created["name"]}
    assert marker(database) == "before"


def test_cli_missing_backup_is_nonzero_without_traceback(project):
    database = live_database(project)
    create_database(database, "live")

    result = run_cli(
        "backup",
        "restore",
        "--config",
        str(project["config_path"]),
        "missing.sqlite",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "rejected"
    assert "missing.sqlite" in payload["reason"]
    assert "traceback" not in result.stderr.lower()
