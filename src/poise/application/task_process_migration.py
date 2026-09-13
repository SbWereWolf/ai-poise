"""Application command for explicit Task process-snapshot migration."""
from __future__ import annotations

from ..modules.tasks.process_migration import MigrationRequest
from ..modules.tasks.ports import TaskProcessMigrationPort


class TaskProcessMigrationCommands:
    def __init__(self, migration: TaskProcessMigrationPort):
        self.migration = migration

    def migrate(self, request: object) -> dict:
        return self.migration.migrate(MigrationRequest.parse(request))
