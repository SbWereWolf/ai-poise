"""Test-only SQLite adapter for isolated Requirements contract checks."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from poise.modules.foundation.errors import DomainError, PoiseError


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TaskRequirementsSnapshotStore:
    """Isolated Task snapshot adapter used outside the versioned Task schema."""

    def __init__(self, database):
        self.database = Path(database)

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database, timeout=0, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if version != 0 or tables - {"task_requirements_snapshots"}:
                connection.execute("ROLLBACK")
                raise PoiseError(
                    "Isolated Task snapshot adapter cannot modify the versioned Task DB"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS task_requirements_snapshots("
                "task_id TEXT PRIMARY KEY, data TEXT NOT NULL, agreement TEXT NOT NULL)"
            )
            connection.execute("COMMIT")

    def create(self, task_id, snapshot, agreement=None):
        if not isinstance(task_id, str) or not task_id:
            raise DomainError("Task snapshot requires a task id")
        self.initialize()
        try:
            with sqlite3.connect(self.database, timeout=0, isolation_level=None) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO task_requirements_snapshots(task_id,data,agreement) VALUES(?,?,?)",
                    (task_id, _encoded(snapshot), _encoded(agreement)),
                )
                connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise DomainError("Task requirements snapshot is immutable and already exists") from exc

    def exists(self, task_id):
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            return connection.execute(
                "SELECT 1 FROM task_requirements_snapshots WHERE task_id=?",
                (task_id,),
            ).fetchone() is not None

    def read(self, task_id):
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT data FROM task_requirements_snapshots WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise DomainError("Task requirements snapshot does not exist")
        return json.loads(row[0])


