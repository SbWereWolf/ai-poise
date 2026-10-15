"""Application command for explicit Task workflow-snapshot migration."""
from __future__ import annotations

from ..modules.tasks.workflow_migration import MigrationRequest
from ..modules.tasks.ports import TaskWorkflowMigrationPort


class TaskWorkflowMigrationCommands:
    def __init__(self, migration: TaskWorkflowMigrationPort):
        self.migration = migration

    def migrate(self, request: object) -> dict:
        return self.migration.migrate(MigrationRequest.parse(request))
