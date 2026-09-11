from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime(2026, 9, 12, 12, 34, 56, 123456, tzinfo=timezone.utc)


def configured_path(config_path: Path, key: str) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    state = Path(config["paths"]["state"])
    if not state.is_absolute():
        state = config_path.parent / state
    value = Path(config["paths"][key])
    return value if value.is_absolute() else state / value


def live_database(project: dict) -> Path:
    return configured_path(project["config_path"], "database")


def backup_directory(project: dict) -> Path:
    return live_database(project).parent / "backups"


def create_database(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES(?)", (marker,))
        connection.commit()
    finally:
        connection.close()


def marker(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM marker").fetchone()[0]
    finally:
        connection.close()


def integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


def expected_backup_name(database: Path) -> str:
    stamp = FIXED_NOW.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{database.stem}-backup-{stamp}{database.suffix}"


def backup_commands(project: dict):
    try:
        from poise.application.backups import BackupCommands
        from poise.infrastructure.backups import LocalTaskDatabaseBackups
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"public backup service is absent: {exc}")
    port = LocalTaskDatabaseBackups(project["config_path"])
    return BackupCommands(port, clock=lambda: FIXED_NOW)


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "poise", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def json_output(result: subprocess.CompletedProcess[str]):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
