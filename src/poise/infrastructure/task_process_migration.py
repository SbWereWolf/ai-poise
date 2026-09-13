"""Locked SQLite adapter for the explicit Task process-snapshot migration."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable

from ..common import configured_root, descendant, digest, encoded, load_config, read_json
from ..modules.foundation.errors import PoiseError
from ..modules.tasks.process_migration import MigrationRequest, ProcessSnapshotMigration
from .locking import exclusive_lock


def _read_only_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _database_snapshot(connection: sqlite3.Connection) -> tuple:
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    data = tuple(
        (table, tuple(tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')))
        for table in tables
    )
    return connection.execute("PRAGMA user_version").fetchone()[0], schema, data


class SqliteTaskProcessMigration:
    def __init__(
        self,
        config_path: Path,
        clock: Callable[[], datetime] | None = None,
        after_update: Callable[[str], None] | None = None,
        after_preflight: Callable[[], None] | None = None,
    ):
        self.config_path = Path(config_path).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.after_update = after_update or (lambda task_id: None)
        self.after_preflight = after_preflight or (lambda: None)

    def migrate(self, request: MigrationRequest) -> dict:
        root, config, processes = load_config(self.config_path)
        try:
            state = configured_root(root, config["paths"]["state"])
            database = descendant(state, config["paths"]["database"])
            lock = descendant(state, config["paths"]["lock"])
            wait = config["limits"]["lock_seconds"]
            poll = config["limits"]["lock_poll_seconds"]
        except (KeyError, TypeError) as exc:
            raise PoiseError(f"Incomplete project manifest for Task process migration: {exc}") from exc
        backup = database.parent / "backups" / request.backup_name
        if backup.is_symlink() or not backup.is_file():
            raise PoiseError(f"Named Task DB backup is missing or not a regular file: {request.backup_name}")
        if database.is_symlink() or not database.is_file():
            raise PoiseError("Configured Task DB is missing or not a regular file")

        try:
            with exclusive_lock(lock, wait, poll):
                with closing(sqlite3.connect(_read_only_uri(backup), uri=True)) as backup_db:
                    if backup_db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise PoiseError(f"Named Task DB backup failed integrity_check: {request.backup_name}")
                    backup_snapshot = _database_snapshot(backup_db)

                with closing(sqlite3.connect(_read_only_uri(database), uri=True)) as live:
                    replay = self._replay(live, request)
                    if replay is not None:
                        return replay
                    if _database_snapshot(live) != backup_snapshot:
                        raise PoiseError("Task DB differs from the named backup before migration preflight")
                    placeholders = ",".join("?" for _ in request.task_ids)
                    selected = live.execute(
                        f"SELECT id,status,claimed_by,metadata FROM tasks WHERE id IN ({placeholders})",
                        request.task_ids,
                    ).fetchall()
                    rows = [
                        {
                            "id": row[0],
                            "status": row[1],
                            "claimed_by": row[2],
                            "metadata": json.loads(row[3]),
                        }
                        for row in selected
                    ]
                plan = ProcessSnapshotMigration.plan(request, rows, processes)
                self.after_preflight()

                connection = sqlite3.connect(database, timeout=0, isolation_level=None, autocommit=True)
                try:
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("BEGIN IMMEDIATE")
                    if _database_snapshot(connection) != backup_snapshot:
                        raise PoiseError("Task DB differs from the named backup after migration preflight")
                    task_results = []
                    for update in plan.updates:
                        raw = connection.execute(
                            "SELECT metadata FROM tasks WHERE id=?", (update.task_id,)
                        ).fetchone()[0]
                        metadata = json.loads(raw)
                        metadata["process"] = update.process
                        connection.execute(
                            "UPDATE tasks SET metadata=? WHERE id=?",
                            (encoded(metadata), update.task_id),
                        )
                        task_results.append(
                            {
                                "task_id": update.task_id,
                                "worktree_required": update.process["worktree_required"],
                                "old_process_digest": update.old_process_digest,
                                "new_process_digest": update.new_process_digest,
                            }
                        )
                        self.after_update(update.task_id)
                    result = {
                        "status": "migrated",
                        "replayed": False,
                        "request_id": request.request_id,
                        "backup_name": request.backup_name,
                        "tasks": task_results,
                    }
                    result["receipt"] = digest(
                        {
                            "request_digest": request.digest,
                            "backup_name": request.backup_name,
                            "tasks": task_results,
                        }
                    )
                    audit = {
                        "request_digest": request.digest,
                        "request_id": request.request_id,
                        "result": result,
                    }
                    connection.execute(
                        "INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
                        (
                            self.clock().isoformat(),
                            "task-process-migrate",
                            None,
                            "task_process_snapshot_migrated",
                            encoded(audit),
                        ),
                    )
                    connection.execute("COMMIT")
                    return result
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                finally:
                    connection.close()
        except PoiseError:
            raise
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise PoiseError(f"Task process migration failed: {exc}") from exc

    @staticmethod
    def _replay(connection: sqlite3.Connection, request: MigrationRequest) -> dict | None:
        rows = connection.execute(
            "SELECT data FROM journal WHERE event='task_process_snapshot_migrated' ORDER BY seq DESC"
        ).fetchall()
        for row in rows:
            audit = json.loads(row[0])
            if audit.get("request_id") != request.request_id:
                continue
            if audit.get("request_digest") != request.digest:
                raise PoiseError("Task process migration request_id belongs to a different intent")
            result = audit.get("result")
            if not isinstance(result, dict):
                raise PoiseError("Stored Task process migration receipt is incompatible")
            return {**result, "replayed": True}
        return None
