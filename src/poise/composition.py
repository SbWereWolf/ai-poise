from .application.tasks import TaskCommands


def task_tools(store, repository_tree, requirements_gate):
    """Explicit composition for the extracted Task slice; no service locator."""
    return TaskCommands(store.unit_of_work, repository_tree, requirements_gate), store.queries


def requirements_tools(config_path):
    from .application.requirements_registry import RequirementsCommands
    from .common import configured_root, configured_storage_path, descendant, load_config
    from .infrastructure.requirements_registry import RequirementsStore
    root, config, _ = load_config(config_path)
    paths = config['paths']
    state = configured_root(root, paths['state'])
    limits = config['limits']
    return RequirementsCommands(
        RequirementsStore(
            configured_storage_path(state, paths['requirements_database']),
            configured_storage_path(state, paths['requirements_lock']),
            limits['lock_seconds'],
            limits['lock_poll_seconds'],
        ),
        config['batch']['max_items'],
    ), config


def goal_config_tools(settings_path):
    from .application.goal_config import GoalConfigCommands
    from .infrastructure.goal_config import EditorSettings, FileGoalConfigRepository, FileTemplates
    settings = EditorSettings(settings_path)
    return GoalConfigCommands(FileGoalConfigRepository(settings), FileTemplates(settings), settings.raw['max_changes'])


def project_tools(settings_path):
    from .application.projects import ProjectCommands
    from .infrastructure.projects import ProjectSettings, FileProjectSetup
    from .infrastructure.project_availability import ReadOnlyProjectAvailability
    return ProjectCommands(FileProjectSetup(ProjectSettings(settings_path)), ReadOnlyProjectAvailability())


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
    from .infrastructure.clock import SystemClock
    from .infrastructure.task_process_migration import SqliteTaskProcessMigration
    return TaskProcessMigrationCommands(SqliteTaskProcessMigration(config_path, SystemClock()))


def local_assets_tools():
    from .application.local_assets import LocalAssetsRestore
    from .infrastructure.local_assets import FileLocalAssets
    return LocalAssetsRestore(FileLocalAssets())
