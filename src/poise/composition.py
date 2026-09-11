from .application.tasks import TaskCommands


def task_tools(store):
    """Explicit composition for the extracted Task slice; no service locator."""
    return TaskCommands(store.unit_of_work), store.queries


def goal_config_tools(settings_path):
    from .application.goal_config import GoalConfigCommands
    from .infrastructure.goal_config import EditorSettings, FileGoalConfigRepository, FileTemplates
    settings = EditorSettings(settings_path)
    return GoalConfigCommands(FileGoalConfigRepository(settings), FileTemplates(settings), settings.raw['max_changes'])


def project_tools(settings_path):
    from .application.projects import ProjectCommands
    from .infrastructure.projects import ProjectSettings, FileProjectSetup
    return ProjectCommands(FileProjectSetup(ProjectSettings(settings_path)))
