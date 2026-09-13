from .application.tasks import TaskCommands


def task_tools(store, repository_tree):
    """Explicit composition for the extracted Task slice; no service locator."""
    return TaskCommands(store.unit_of_work, repository_tree), store.queries


def goal_config_tools(settings_path):
    from .application.goal_config import GoalConfigCommands
    from .infrastructure.goal_config import EditorSettings, FileGoalConfigRepository, FileTemplates
    settings = EditorSettings(settings_path)
    return GoalConfigCommands(FileGoalConfigRepository(settings), FileTemplates(settings), settings.raw['max_changes'])


def project_tools(settings_path):
    from .application.projects import ProjectCommands
    from .infrastructure.projects import ProjectSettings, FileProjectSetup
    return ProjectCommands(FileProjectSetup(ProjectSettings(settings_path)))


def project_config_tools(settings_path):
    from .application.project_config import ProjectConfigCommands
    from .infrastructure.project_config import FileProjectConfigUpdate
    from .infrastructure.projects import ProjectSettings
    settings = ProjectSettings(settings_path)
    return ProjectConfigCommands(FileProjectConfigUpdate(settings), settings.raw['max_edits'])


def route_migration_tools(config_path):
    from .application.route_migration import RouteMigrationCommands
    from .infrastructure.route_migration import FileRouteCountLimitMigration
    return RouteMigrationCommands(FileRouteCountLimitMigration(config_path))


def task_process_migration_tools(config_path):
    from .application.task_process_migration import TaskProcessMigrationCommands
    from .infrastructure.task_process_migration import SqliteTaskProcessMigration
    return TaskProcessMigrationCommands(SqliteTaskProcessMigration(config_path))
