def tool_result_tools(bound, policy, environment):
    """Taskless output uses the same explicit caller and placement owners."""
    from .application.tool_results import ToolResults
    from .infrastructure.tool_results import FileToolResultStore, ResultPlacement
    from .infrastructure.session_establishment import direct_caller
    caller = direct_caller(bound.config['project'], environment)
    placement = ResultPlacement(placement_tools(bound, policy['placement']['policy'], environment),
                                policy, bound.config['batch']['max_items'])
    return ToolResults(policy, FileToolResultStore(bound, caller, policy, placement))


def project_checks_tools(bound, policy_path, environment, clock):
    """General checks compose existing identity/execution/presentation owners."""
    from .application.project_checks import ProjectChecks
    from .execution import RegisteredCheckRunner
    from .modules.project_checks.timing import TimingPolicy
    from .infrastructure.project_checks.configuration import CheckConfiguration
    from .infrastructure.project_checks.inputs import InputSnapshot
    from .infrastructure.project_checks.runtime import RuntimeAdapter
    from .infrastructure.project_checks.ledger import NamedRunLedger
    from .infrastructure.project_checks.cache import NamedResultCache
    from .infrastructure.project_checks.subject import SubjectAdmission
    from .infrastructure.project_checks.execution import CheckExecution
    from .infrastructure.project_checks.publication import ProfilePublisher
    from .infrastructure.session_establishment import establish_project_session, direct_caller
    configuration = CheckConfiguration.load(bound, policy_path)
    inputs = InputSnapshot(bound.checkout, bound.config['limits']['git_seconds'], configuration.packages)
    runtime = RuntimeAdapter(bound.checkout, configuration.policy['runtime_adapters'])
    ledger = NamedRunLedger(configuration)
    cache = NamedResultCache(configuration, ledger)
    subject = SubjectAdmission(inputs)
    runner = RegisteredCheckRunner(bound.config['limits']['output_chars'])
    execution = CheckExecution(configuration, runner, clock, subject)
    timing = TimingPolicy(configuration.policy['timing']['history_multiplier'],
                          configuration.policy['timing']['diagnostic_override_max_seconds'])
    publisher = ProfilePublisher(configuration, clock)
    service = ProjectChecks(configuration, inputs, runtime, ledger, cache, execution,
                            timing, subject, publisher, environment)
    service.establish_session = lambda: establish_project_session(bound, direct_caller(bound.config['project'], environment))
    return service


def intake_session_tools(configuration, caller, authority_factory, clock):
    """One composition for native admission and episode-owned reading resources."""
    from pathlib import Path
    from .application.document_intake import IntakeSessions
    from .infrastructure.document_intake.resources import EpisodeResources
    from .infrastructure.document_intake.template import PreparedTemplate
    resources = EpisodeResources(configuration, caller, authority_factory, clock)
    template = PreparedTemplate(Path(configuration.policy['session']['registry_template']))
    return resources, IntakeSessions(configuration, resources, template)


def content_reader_tools(configuration, environment, clock):
    """Taskless content reading composes canonical identity and native owners."""
    from .application.content_reader import ContentReader
    from .infrastructure.document_intake.source import ProjectContentSource
    from .infrastructure.document_intake.presentation import FullReadCost
    from .infrastructure.document_intake.native import HookNativeAuthority
    from .infrastructure.hook_transport import HookService
    from .infrastructure.session_establishment import direct_caller, establish_project_session
    from .modules.document_intake.contracts import ReadFailure
    from .common import PoiseError
    from pathlib import Path
    bound = configuration.bound
    try:
        caller = direct_caller(bound.config['project'], environment)
    except PoiseError as exc:
        raise ReadFailure('invalid_caller') from exc
    try:
        establish_project_session(bound, caller)
    except PoiseError as exc:
        raise ReadFailure('admission_unavailable') from exc

    def authority():
        admission = configuration.policy['admission']
        try:
            service = HookService(admission['hook_settings'])
            if service.settings.project_config.resolve() != bound.config_path.resolve():
                raise ReadFailure('authority_mismatch')
            return HookNativeAuthority(service=service, project_id=bound.config['project'],
                                       definition_path=Path(admission['definition_path']),
                                       authority_identity=dict(admission))
        except (PoiseError, OSError, ValueError) as exc:
            raise ReadFailure('admission_unavailable') from exc

    resources, sessions = intake_session_tools(configuration, caller, authority, clock)
    command = None
    if 'command_intake' in configuration.policy:
        from .application.command_intake import CommandIntake
        from .infrastructure.document_intake.command_configuration import load_command_policy
        from .infrastructure.document_intake.command_source import CommandSource
        from .modules.document_intake.command import CommandFailure
        try:
            policy, digest = load_command_policy(configuration.policy)
        except CommandFailure as exc:
            raise ReadFailure('invalid_policy', field=exc.field) from exc
        command = CommandIntake(resources, sessions, CommandSource(bound.checkout, policy), policy, digest, clock)
    return ContentReader(configuration, ProjectContentSource(), sessions, resources, FullReadCost(), command=command)


def task_tools(store, repository_tree, requirements_gate):
    """Explicit composition for the extracted Task slice; no service locator."""
    from .application.tasks import TaskCommands
    return TaskCommands(store.unit_of_work, repository_tree, requirements_gate), store.queries


def placement_tools(bound, policy_path, environ):
    import hashlib
    from pathlib import Path
    from .common import PoiseError, configured_root
    from .modules.placement.contracts import PlacementInputError
    from .modules.placement.policy import PlacementPolicy
    from .application.placement import PlacementCommands
    from .infrastructure.goal_config import strict_json
    from .infrastructure.session_establishment import direct_caller
    from .infrastructure.placement import FilePlacementWorkspace
    from .infrastructure.placement_owners import PoiseCurrentPlacementOwners
    try:
        raw = Path(policy_path).read_bytes()
    except OSError as exc:
        raise PlacementInputError('unreadable_policy', 'policy', 'restore:policy') from exc
    try:
        policy = PlacementPolicy.from_configuration(strict_json(raw.decode('utf-8')))
    except (PoiseError, UnicodeError, RecursionError, TypeError, ValueError) as exc:
        raise PlacementInputError('invalid_policy', 'policy', 'correct:policy') from exc
    for root in policy.roots.values():
        if root['path'] is not None:
            root['path'] = str(Path(root['path']).resolve())
    state = configured_root(bound.root, bound.config['paths']['state'])
    if not Path(policy.state['directory']).resolve().is_relative_to(state):
        raise PlacementInputError('invalid_policy', 'policy', 'correct:policy')
    try:
        caller = direct_caller(bound.config['project'], environ)
    except (PoiseError, OSError, ValueError) as exc:
        raise PlacementInputError('invalid_caller', 'caller', 'provide:caller') from exc
    workspace = FilePlacementWorkspace(bound, policy, caller.key)
    owners = PoiseCurrentPlacementOwners(bound, caller.key) if policy.owner_provider is not None else None
    return PlacementCommands(bound.config['project'], str(bound.checkout), caller.key,
                             policy, hashlib.sha256(raw).hexdigest(), workspace, owners,
                             lambda content: strict_json(content.decode('utf-8')))


def requirements_storage(config_path):
    from .common import configured_root, configured_storage_path, load_config
    root, config, _ = load_config(config_path)
    paths = config['paths']
    state = configured_root(root, paths['state'])
    limits = config['limits']
    return (
        configured_storage_path(state, paths['requirements_database']),
        configured_storage_path(state, paths['requirements_lock']),
        limits['lock_seconds'], limits['lock_poll_seconds'],
    ), config


def requirements_tools(config_path, *, read_only=False):
    from .application.requirements_registry import RequirementsCommands
    from .infrastructure.requirements_registry import RequirementsStore
    storage, config = requirements_storage(config_path)
    from .infrastructure.project_resources import ProjectRuntimeFence
    fence = ProjectRuntimeFence(config_path)
    return RequirementsCommands(RequirementsStore(*storage, project_fence=fence,
                                                 read_only=read_only,
                                                 read_wait=config['limits']['sqlite_read_seconds']),
                                config['batch']['max_items']), config


def migrate_requirements(config_path):
    from .infrastructure.requirements_registry import RequirementsStore
    storage, _ = requirements_storage(config_path)
    return RequirementsStore.migrate(*storage)


def goal_config_tools(settings_path):
    from .application.goal_config import GoalConfigCommands
    from .infrastructure.goal_config import EditorSettings, FileGoalConfigRepository, FileTemplates
    settings = EditorSettings(settings_path)
    return GoalConfigCommands(FileGoalConfigRepository(settings), FileTemplates(settings), settings.raw['max_changes'])


def project_tools(settings_path):
    from .infrastructure.projects import ProjectSettings
    return compose_project_tools(ProjectSettings(settings_path))


def compose_project_tools(settings):
    from pathlib import Path
    from .application.projects import ProjectCommands
    from .infrastructure.projects import FileProjectSetup
    from .infrastructure.project_availability import ReadOnlyProjectAvailability
    from .infrastructure.project_context import FileProjectContext
    from .infrastructure.project_preflight import FileProjectPreflight
    setup = FileProjectSetup(settings)
    availability = ReadOnlyProjectAvailability()
    context = FileProjectContext(Path(__file__).resolve().parents[1])
    return ProjectCommands(setup, availability,
                           FileProjectPreflight(settings, setup, availability, context))


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


def task_workflow_migration_tools(config_path, project_settings=None):
    from .application.task_workflow_migration import TaskWorkflowMigrationCommands
    from .infrastructure.clock import SystemClock
    from .infrastructure.task_workflow_migration import SqliteTaskWorkflowMigration
    owner = None
    if project_settings is not None:
        from .infrastructure.projects import ProjectSettings
        from .infrastructure.project_config import FileProjectConfigUpdate
        from .modules.foundation.errors import PoiseError
        try:
            settings = ProjectSettings(project_settings)
        except OSError as exc:
            raise PoiseError(f"Cannot load selected project settings: {exc}") from exc
        owner = FileProjectConfigUpdate(settings)
    return TaskWorkflowMigrationCommands(SqliteTaskWorkflowMigration(
        config_path, SystemClock(), project_publication=owner))


def local_assets_tools():
    from .application.local_assets import LocalAssetsRestore
    from .infrastructure.local_assets import FileLocalAssets
    return LocalAssetsRestore(FileLocalAssets())
