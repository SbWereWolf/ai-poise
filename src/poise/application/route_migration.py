from ..modules.workflow.ports import RouteCountLimitMigration


class RouteMigrationCommands:
    def __init__(self, migration: RouteCountLimitMigration):
        self.migration = migration

    def migrate(self) -> dict:
        return self.migration.migrate()
