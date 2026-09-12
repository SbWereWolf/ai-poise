"""Locked, idempotent migration of configured processes and stored task snapshots."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..common import configured_root, descendant, load_config, read_json
from ..modules.foundation.errors import PoiseError
from ..modules.workflow.migration import migrate_process_route
from .goal_config import atomic_write
from .locking import exclusive_lock


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


class FileRouteCountLimitMigration:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path).resolve()

    def migrate(self) -> dict:
        config = read_json(self.config_path)
        root = self.config_path.parent
        try:
            state = configured_root(root, config["paths"]["state"])
            lock = descendant(state, config["paths"]["lock"])
            database = descendant(state, config["paths"]["database"])
            wait = config["limits"]["lock_seconds"]
            poll = config["limits"]["lock_poll_seconds"]
            process_paths = {
                goal: descendant(root, relative)
                for goal, relative in config["processes"].items()
            }
        except (KeyError, TypeError, AttributeError) as exc:
            raise PoiseError(f"Incomplete project manifest for route migration: {exc}") from exc

        with exclusive_lock(lock, wait, poll):
            processes = {}
            changed_processes = 0
            for goal, path in process_paths.items():
                candidate, changed = migrate_process_route(read_json(path))
                if candidate["goal_type"] != goal:
                    raise PoiseError(f"Ambiguous configured process {goal}")
                processes[path] = candidate
                changed_processes += int(changed)

            connection = None
            task_updates = []
            try:
                if database.exists():
                    if database.is_symlink() or not database.is_file():
                        raise PoiseError("Configured Task DB must be a regular file")
                    connection = sqlite3.connect(database)
                    connection.execute("BEGIN IMMEDIATE")
                    for task_id, raw in connection.execute("SELECT id,metadata FROM tasks ORDER BY id"):
                        metadata = json.loads(raw)
                        process, changed = migrate_process_route(metadata["process"])
                        if changed:
                            candidate = {**metadata, "process": process}
                            task_updates.append((json.dumps(candidate, ensure_ascii=False,
                                sort_keys=True, separators=(",", ":")), task_id))

                for path, process in processes.items():
                    current = read_json(path)
                    if current != process:
                        atomic_write(path, encoded(process), path.stat().st_mode & 0o777)

                load_config(self.config_path)
                if connection is not None:
                    connection.executemany("UPDATE tasks SET metadata=? WHERE id=?", task_updates)
                    connection.commit()
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
                if connection is not None:
                    connection.rollback()
                raise PoiseError(f"Route migration failed; rerun after correcting the cause: {exc}") from exc
            finally:
                if connection is not None:
                    connection.close()

        return {
            "status": "migrated",
            "processes": changed_processes,
            "task_snapshots": len(task_updates),
        }
