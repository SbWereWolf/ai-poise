from __future__ import annotations
import hashlib
import math
import json
import re
from pathlib import Path


from .modules.foundation.errors import PoiseError
from .modules.foundation.validation import validate_exact_keys


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


from .modules.verification.domain import validate_method


def load_config(path: Path, legacy_process_requirements: dict[str, bool] | None = None) -> tuple[Path, dict, dict]:
    root = path.resolve().parent
    cfg = read_json(path)
    keys = {'schema','project','paths','limits','git','processes','environment_names',
            'automatic_checks','batch','sprint','runtime_services','accounting'}
    exact_keys(cfg, keys | ({'task_ids'} if 'task_ids' in cfg else set()), 'project config')
    if cfg['schema'] != 'ddd-accounting-12':
        raise PoiseError('Версия конфигурации не поддерживается; автоматических миграций нет')
    task_paths = {'state','database','lock','runtime','tasks','sprints','worktrees',
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
    try:
        re.compile(cfg['git']['commit_pattern'])
        cfg['git']['branch_template'].format(task_id='probe', session_id='probe')
    except (KeyError,ValueError,re.error) as exc:
        raise PoiseError(f'Некорректное правило Git: {exc}') from exc
    state = configured_root(root, cfg['paths']['state'])
    if state.is_relative_to(path.resolve()) or path.resolve().is_relative_to(state):
        raise PoiseError('Mutable state root overlaps the project manifest')
    for key in ('database','lock','runtime','tasks','sprints','worktrees'):
        descendant(state, cfg['paths'][key])
    requirements_storage = [
        descendant(state, cfg['paths'][key])
        for key in ('requirements_database', 'requirements_lock')
    ]
    task_storage = [
        descendant(state, cfg['paths'][key])
        for key in ('database', 'lock')
    ]
    if len(set(requirements_storage + task_storage)) != 4:
        raise PoiseError('Requirements DB/lock должны быть отделены от Task DB/lock')
    for left in requirements_storage:
        for right in task_storage:
            try:
                aliased = left.exists() and right.exists() and left.samefile(right)
            except OSError:
                aliased = False
            if aliased:
                raise PoiseError('Requirements storage физически совпадает с Task storage')
    for key in ('git_index','runs','stdout','stderr','response'):
        descendant(state, cfg['paths'][key])
    homes = [descendant(state, cfg['paths'][k]) for k in ('runtime','tasks','sprints','worktrees')]
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
    authoritative=[descendant(state,cfg['paths'][key]) for key in ('database','lock')]
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
    exact_keys(cfg['runtime_services'],{'output','handoff','transfer'},'runtime_services')
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
    from .modules.work.domain import validate_config
    validate_config(cfg['batch'])
    processes = {}
    for kind, rel in cfg['processes'].items():
        process_path=descendant(root, rel)
        if state.is_relative_to(process_path) or process_path.is_relative_to(state):
            raise PoiseError('Mutable state root overlaps a process configuration')
        process = read_json(process_path)
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
