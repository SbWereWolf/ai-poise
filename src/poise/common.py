from __future__ import annotations
import hashlib
import math
import json
import re
from pathlib import Path


from .modules.foundation.errors import PoiseError
from .modules.foundation.validation import validate_exact_keys


GIT_PUSH_PROHIBITION = (
    "Git push is prohibited; set push_required=false and use the local integrate "
    "operation, which publishes with git merge --ff-only."
)


def prohibit_git_push(argv) -> None:
    """Reject a direct Git push before any process or Git adapter is invoked."""
    if (isinstance(argv, (list, tuple)) and argv
            and isinstance(argv[0], str) and Path(argv[0]).name == "git"
            and any(argument == "push" for argument in argv[1:])):
        raise PoiseError(GIT_PUSH_PROHIBITION)


def prohibit_remote_git_publication() -> None:
    raise PoiseError(GIT_PUSH_PROHIBITION)


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    validate_exact_keys(value, keys, where, PoiseError)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PoiseError(f'Не удалось прочитать JSON {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise PoiseError(f'{path}: требуется JSON-объект')
    return value


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def descendant(root: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise PoiseError(f'Требуется относительный путь внутри root: {relative}')
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise PoiseError(f'Путь выходит из root: {relative}')
    return resolved


def worktree_root(state: Path, config: dict) -> Path:
    """Resolve an explicit workspace root without relaxing mutable-store confinement."""
    path = Path(config['paths']['worktrees'])
    if not path.is_absolute():
        return descendant(state, str(path))
    repository = Path(config['git']['repository']).resolve()
    resolved = path.resolve()
    if (resolved == repository or not resolved.is_relative_to(repository)
            or resolved.is_relative_to(repository / '.git')
            or '..' in path.parts
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        raise PoiseError('Worktree root must be a symlink-free descendant of the repository')
    return resolved


def configured_root(config_root: Path, value: str) -> Path:
    """Resolve one explicitly configured mutable root.

    Project manifests may live with Poise while mutable project state lives
    elsewhere (for example beside the target system in WSL). Relative values
    remain relative to the project manifest; absolute values are used exactly.
    No environment expansion or fallback is performed here.
    """
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise PoiseError('paths.state: требуется явный непустой путь')
    candidate = Path(value)
    if not candidate.is_absolute():
        return descendant(config_root, value)
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise PoiseError('paths.state: корень файловой системы недопустим')
    return resolved


def configured_storage_path(state: Path, value: str) -> Path:
    """Resolve an explicit store file, with no default or implicit relocation."""
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise PoiseError('Requirements storage: explicit nonempty file path required')
    path = Path(value)
    if not path.is_absolute():
        return descendant(state, value)
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise PoiseError('Requirements storage: filesystem root is not a store file')
    return resolved


from .modules.verification.domain import validate_method


def load_config(path: Path, legacy_process_requirements: dict[str, bool] | None = None) -> tuple[Path, dict, dict]:
    return _load_config(path, path.resolve().parent, legacy_process_requirements)


def validate_candidate_config(path: Path, final_root: Path) -> tuple[Path, dict, dict]:
    """Validate staged bytes at their explicit final configuration coordinates."""
    return _load_config(path, final_root, None)


def _load_config(path: Path, final_root: Path,
                 legacy_process_requirements: dict[str, bool] | None) -> tuple[Path, dict, dict]:
    root = final_root.resolve()
    material_root = path.resolve().parent
    final_manifest = root / path.name
    cfg = read_json(path)
    keys = {'schema','project','paths','limits','git','processes','environment_names',
            'automatic_checks','batch','sprint','runtime_services','accounting',
            'task_decomposition'}
    exact_keys(cfg, keys | ({'task_ids', 'development_routing', 'source_reader', 'telemetry_delivery', 'task_planning'} & cfg.keys()), 'project config')
    if 'task_planning' in cfg:
        from .modules.tasks.planning import validate_planning
        settings = cfg['task_planning']
        exact_keys(settings, {'catalogue', 'restart_revision_policy'}, 'task_planning')
        if not isinstance(settings['catalogue'], str) or not settings['catalogue'].strip():
            raise PoiseError('task_planning.catalogue requires an explicit path')
        validate_planning({'schema': 'task-planning-1', 'template': None,
                           'restart_revision_policy': settings['restart_revision_policy']})
    if 'telemetry_delivery' in cfg:
        from .modules.accounting.delivery import TelemetryDeliveryPolicy
        delivery = cfg['telemetry_delivery']
        exact_keys(delivery, {'directory', 'policy'}, 'telemetry_delivery')
        if not isinstance(delivery['directory'], str) or not delivery['directory'].strip() or '\0' in delivery['directory']:
            raise PoiseError('telemetry_delivery.directory: explicit path required')
        TelemetryDeliveryPolicy.parse(delivery['policy'])
    if 'source_reader' in cfg:
        exact_keys(cfg['source_reader'], {'policy', 'receipt_file'}, 'source_reader')
        for name, value in cfg['source_reader'].items():
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise PoiseError(f'source_reader.{name}: explicit path required')
        descendant(root, cfg['source_reader']['receipt_file'])
    if 'development_routing' in cfg:
        exact_keys(cfg['development_routing'], {'catalog','selection','policy','packages'},
                   'development_routing')
        for name, value in cfg['development_routing'].items():
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise PoiseError(f'development_routing.{name}: explicit filesystem path required')
    if cfg['schema'] != 'ddd-accounting-12':
        raise PoiseError('Версия конфигурации не поддерживается; автоматических миграций нет')
    task_paths = {'state','database','lock','runtime','standalone_tasks','sprints','worktrees',
                  'git_index','runs','stdout','stderr','response'}
    requirements_paths = {'requirements_database', 'requirements_lock'}
    exact_keys(cfg['paths'], task_paths | requirements_paths, 'paths')
    exact_keys(cfg['limits'], {'lock_seconds','lock_poll_seconds','git_seconds','verify_attempts',
                              'output_chars','preview_chars'}, 'limits')
    for key, value in cfg['limits'].items():
        if not isinstance(value, (int,float)) or isinstance(value,bool) or value <= 0 or not math.isfinite(value):
            raise PoiseError(f'limits.{key}: требуется конечное положительное число')
    for key in ('verify_attempts','output_chars','preview_chars'):
        if not isinstance(cfg['limits'][key], int):
            raise PoiseError(f'limits.{key}: требуется целое число')
    exact_keys(cfg['git'], {'repository','base_ref','remote','branch_template','commit_pattern',
                           'author_name','author_email','push_required'}, 'git')
    if not isinstance(cfg['project'],str) or not cfg['project'].strip():
        raise PoiseError('project: требуется явный непустой идентификатор')
    for key in ('repository','base_ref','remote','branch_template','commit_pattern','author_name','author_email'):
        if not isinstance(cfg['git'][key],str) or not cfg['git'][key].strip():
            raise PoiseError(f'git.{key}: требуется непустая строка')
    if not Path(cfg['git']['repository']).is_absolute():
        raise PoiseError('git.repository: требуется абсолютный путь')
    if type(cfg['git']['push_required']) is not bool:
        raise PoiseError('git.push_required: требуется явное true/false')
    if cfg['git']['push_required']:
        prohibit_remote_git_publication()
    try:
        re.compile(cfg['git']['commit_pattern'])
        cfg['git']['branch_template'].format(task_id='probe', session_id='probe')
    except (KeyError,ValueError,re.error) as exc:
        raise PoiseError(f'Некорректное правило Git: {exc}') from exc
    state = configured_root(root, cfg['paths']['state'])
    if state.is_relative_to(final_manifest) or final_manifest.is_relative_to(state):
        raise PoiseError('Mutable state root overlaps the project manifest')
    for key in ('database','lock','runtime','standalone_tasks','sprints'):
        descendant(state, cfg['paths'][key])
    requirements_storage = [
        configured_storage_path(state, cfg['paths'][key])
        for key in ('requirements_database', 'requirements_lock')
    ]
    task_storage = [
        descendant(state, cfg['paths'][key])
        for key in ('database', 'lock')
    ]
    if len(set(requirements_storage + task_storage)) != 4:
        raise PoiseError('Requirements DB/lock должны быть отделены от Task DB/lock')
    codebase = Path(cfg['git']['repository']).resolve()
    internal = tuple(path.relative_to(codebase).as_posix()
                     for path in requirements_storage if path.is_relative_to(codebase))
    if internal:
        from .infrastructure.repository_tree import GitRepositoryTree
        try:
            facts = GitRepositoryTree(
                codebase, cfg['limits']['git_seconds'], cfg['limits']['preview_chars'],
            ).current_path_facts(internal)
        except (OSError, PoiseError) as exc:
            raise PoiseError(f'Requirements storage: не удалось проверить Git: {exc}') from exc
        for name in internal:
            if name in facts['tracked']:
                raise PoiseError(f'Requirements storage: Git отслеживает путь в codebase: {name}')
            if name not in facts['ignored']:
                raise PoiseError(f'Requirements storage: Git не игнорирует путь в codebase: {name}')
    for index, left in enumerate(requirements_storage):
        for right in [*task_storage, *requirements_storage[index + 1:]]:
            try:
                aliased = left.exists() and right.exists() and left.samefile(right)
            except OSError:
                aliased = False
            if aliased:
                raise PoiseError('Requirements storage физически совпадает с Task storage')
    for key in ('git_index','runs','stdout','stderr','response'):
        descendant(state, cfg['paths'][key])
    homes = [descendant(state, cfg['paths'][k]) for k in ('runtime','standalone_tasks','sprints')]
    homes.append(worktree_root(state, cfg))
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(homes) for b in homes[i+1:]):
        raise PoiseError('Корни runtime/task/sprint/worktree не должны пересекаться')
    if not isinstance(cfg['processes'],dict) or not cfg['processes']:
        raise PoiseError('Нет явно заданных процессов')
    if legacy_process_requirements is not None:
        if (set(legacy_process_requirements) != set(cfg['processes'])
                or any(type(value) is not bool for value in legacy_process_requirements.values())):
            raise PoiseError('Bound-source migration requires one explicit bool for every process')
    from .modules.accounting.domain import MetricPolicy
    accounting=MetricPolicy.parse(cfg['accounting']).data
    optional=[descendant(state,accounting['storage'][key]) for key in ('database','lock')]
    authoritative = task_storage + requirements_storage
    if optional[0]==optional[1] or any(item in authoritative for item in optional):
        raise PoiseError('accounting.storage должен использовать отдельные database и lock')
    for index,left in enumerate(optional):
        for right in [*authoritative,*optional[index+1:]]:
            try:
                aliased=left.exists() and right.exists() and left.samefile(right)
            except OSError:
                aliased=False
            if aliased:
                raise PoiseError('accounting.storage физически совпадает с другим хранилищем')
    exact_keys(cfg['runtime_services'],{'output','handoff','transfer','check_runner'},'runtime_services')
    check_runner=cfg['runtime_services']['check_runner']
    exact_keys(check_runner,{'initial_seconds','history_multiplier','progress_gap_seconds','poll_seconds','diagnostic_override_max_seconds'},'check_runner config')
    for key in ('initial_seconds','history_multiplier','progress_gap_seconds','poll_seconds','diagnostic_override_max_seconds'):
        value=check_runner[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise PoiseError(f'check_runner.{key} must be a positive finite number')
    if check_runner['history_multiplier']<1:
        raise PoiseError('check_runner.history_multiplier must be at least 1')
    if check_runner['diagnostic_override_max_seconds']<check_runner['initial_seconds']:
        raise PoiseError('check_runner diagnostic override maximum must not be below initial timeout')
    from .modules.transfers.domain import TransferPolicy
    TransferPolicy.parse(cfg['runtime_services']['transfer'])
    transfer_root=descendant(state,cfg['runtime_services']['transfer']['directory'])
    if any(transfer_root.is_relative_to(p) or p.is_relative_to(transfer_root) for p in homes):
        raise PoiseError('Transfer staging must not overlap runtime/task/sprint/worktree roots')
    from .modules.result_views.domain import OutputPolicy
    OutputPolicy.parse(cfg['runtime_services']['output'])
    transfer=cfg['runtime_services']['handoff']
    exact_keys(transfer,{'directory','receipt','bundle','preserved_directory','file_mode'},'handoff config')
    for key in ('directory','receipt','bundle','preserved_directory'):descendant(state,transfer[key])
    if len({transfer['receipt'],transfer['bundle'],transfer['preserved_directory']})!=3:
        raise PoiseError('Handoff filenames must be distinct')
    if type(transfer['file_mode']) is not int or not 0<=transfer['file_mode']<=0o777:
        raise PoiseError('Explicit handoff file_mode required')
    from .modules.sprints.domain import SprintPolicy
    SprintPolicy.parse(cfg['sprint'])
    if 'task_ids' in cfg:
        from .modules.tasks.allocation import TaskIdPolicy
        TaskIdPolicy.parse(cfg['task_ids'])
    from .modules.tasks.decomposition import FocusedDecomposition
    FocusedDecomposition.validate_policy(cfg['task_decomposition'])
    from .modules.work.domain import validate_config
    validate_config(cfg['batch'])
    processes = {}
    for kind, rel in cfg['processes'].items():
        process_path=descendant(root, rel)
        if state.is_relative_to(process_path) or process_path.is_relative_to(state):
            raise PoiseError('Mutable state root overlaps a process configuration')
        process = read_json(descendant(material_root, rel))
        from .modules.goal_config.domain import GoalTypeDefinition, PROCESS_FIELDS
        if (legacy_process_requirements is not None
                and set(process) == PROCESS_FIELDS - {'worktree_required'}):
            process['worktree_required'] = legacy_process_requirements[kind]
        process = GoalTypeDefinition.parse(process).data
        if process['goal_type'] != kind:
            raise PoiseError(f'Неоднозначный process {kind}')
        processes[kind] = process
    for entry in cfg['automatic_checks']:
        exact_keys(entry, {'paths','by_stage'}, 'automatic checks')
    return root, cfg, processes
