from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..modules.backups.domain import exact_backup_name, generated_backup_name
from ..modules.backups.ports import TaskDatabaseBackups


class BackupCommands:
    def __init__(self, backups: TaskDatabaseBackups, clock: Callable[[], datetime]):
        self.backups = backups
        self.clock = clock

    def list(self) -> list[dict]:
        return [
            {"name": item.name, "created_at": item.created_at}
            for item in self.backups.list_entries()
        ]

    def create(self) -> dict:
        name = generated_backup_name(self.backups.database_name, self.clock())
        copy = self.backups.create_copy(name)
        return {
            "success": True,
            "name": copy.name,
            "live_size_bytes": copy.live_size_bytes,
            "backup_size_bytes": copy.backup_size_bytes,
        }

    def restore(self, name: str) -> dict:
        selected = exact_backup_name(name)
        self.backups.restore_copy(selected)
        return {"success": True, "name": selected}
