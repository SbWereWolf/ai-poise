from __future__ import annotations
import hashlib
import math
import json
import re
from pathlib import Path


from .modules.foundation.errors import HarnessError


def exact_keys(value: dict, keys: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise HarnessError(f'{where}: ожидается объект')
    missing, extra = keys - value.keys(), value.keys() - keys
    if missing or extra:
        raise HarnessError(f'{where}: отсутствуют {sorted(missing)}; неизвестные поля {sorted(extra)}')


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise HarnessError(f'Не удалось прочитать JSON {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise HarnessError(f'{path}: требуется JSON-объект')
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
        raise HarnessError(f'Требуется относительный путь внутри root: {relative}')
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise HarnessError(f'Путь выходит из root: {relative}')
    return resolved


def configured_root(config_root: Path, value: str) -> Path:
    """Resolve one explicitly configured mutable root.

    Project manifests may live with Harness while mutable project state lives
    elsewhere (for example beside the target system in WSL). Relative values
    remain relative to the project manifest; absolute values are used exactly.
    No environment expansion or fallback is performed here.
    """
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise HarnessError('paths.state: требуется явный непустой путь')
    candidate = Path(value)
    if not candidate.is_absolute():
        return descendant(config_root, value)
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise HarnessError('paths.state: корень файловой системы недопустим')
    return resolved


from .modules.verification.domain import validate_method


def load_config(path: Path) -> tuple[Path, dict, dict]:
    root = path.resolve().parent
    cfg = read_json(path)
    keys = {'schema','project','paths','limits','git','processes','environment_names',
            'automatic_checks','batch','sprint','runtime_services','accounting'}
    exact_keys(cfg, keys | ({'task_ids'} if 'task_ids' in cfg else set()), 'project config')
    if cfg['schema'] != 'ddd-accounting-11':
        raise HarnessError('Неподдерживаемая schema; автоматических миграций нет')
    exact_keys(cfg['paths'], {'state','database','lock','runtime','tasks','sprints','worktrees',
                             'git_index','runs','stdout','stderr','response'}, 'paths')
    exact_keys(cfg['limits'], {'lock_seconds','lock_poll_seconds','git_seconds','verify_attempts',
                              'output_chars','preview_chars'}, 'limits')
    for key, value in cfg['limits'].items():
        if not isinstance(value, (int,float)) or isinstance(value,bool) or value <= 0 or not math.isfinite(value):
            raise HarnessError(f'limits.{key}: требуется конечное положительное число')
    for key in ('verify_attempts','output_chars','preview_chars'):
        if not isinstance(cfg['limits'][key], int):
            raise HarnessError(f'limits.{key}: требуется целое число')
    exact_keys(cfg['git'], {'repository','base_ref','remote','branch_template','commit_pattern',
                           'author_name','author_email','push_required'}, 'git')
    if not isinstance(cfg['project'],str) or not cfg['project'].strip():
        raise HarnessError('project: требуется явный непустой идентификатор')
    for key in ('repository','base_ref','remote','branch_template','commit_pattern','author_name','author_email'):
        if not isinstance(cfg['git'][key],str) or not cfg['git'][key].strip():
            raise HarnessError(f'git.{key}: требуется непустая строка')
    if not Path(cfg['git']['repository']).is_absolute():
        raise HarnessError('git.repository: требуется абсолютный путь')
    if type(cfg['git']['push_required']) is not bool:
        raise HarnessError('git.push_required: требуется явное true/false')
    try:
        re.compile(cfg['git']['commit_pattern'])
        cfg['git']['branch_template'].format(task_id='probe', session_id='probe')
    except (KeyError,ValueError,re.error) as exc:
        raise HarnessError(f'Некорректное правило Git: {exc}') from exc
    state = configured_root(root, cfg['paths']['state'])
    if state.is_relative_to(path.resolve()) or path.resolve().is_relative_to(state):
        raise HarnessError('Mutable state root overlaps the project manifest')
    for key in ('database','lock','runtime','tasks','sprints','worktrees'):
        descendant(state, cfg['paths'][key])
    for key in ('git_index','runs','stdout','stderr','response'):
        descendant(state, cfg['paths'][key])
    homes = [descendant(state, cfg['paths'][k]) for k in ('runtime','tasks','sprints','worktrees')]
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(homes) for b in homes[i+1:]):
        raise HarnessError('Корни runtime/task/sprint/worktree не должны пересекаться')
    if not isinstance(cfg['processes'],dict) or not cfg['processes']:
        raise HarnessError('Нет явно заданных процессов')
    from .modules.accounting.domain import MetricPolicy
    MetricPolicy.parse(cfg['accounting'])
    exact_keys(cfg['runtime_services'],{'output','handoff','transfer'},'runtime_services')
    from .modules.transfers.domain import TransferPolicy
    TransferPolicy.parse(cfg['runtime_services']['transfer'])
    transfer_root=descendant(state,cfg['runtime_services']['transfer']['directory'])
    if any(transfer_root.is_relative_to(p) or p.is_relative_to(transfer_root) for p in homes):
        raise HarnessError('Transfer staging must not overlap runtime/task/sprint/worktree roots')
    from .modules.result_views.domain import OutputPolicy
    OutputPolicy.parse(cfg['runtime_services']['output'])
    transfer=cfg['runtime_services']['handoff']
    exact_keys(transfer,{'directory','receipt','bundle','preserved_directory','file_mode'},'handoff config')
    for key in ('directory','receipt','bundle','preserved_directory'):descendant(state,transfer[key])
    if len({transfer['receipt'],transfer['bundle'],transfer['preserved_directory']})!=3:
        raise HarnessError('Handoff filenames must be distinct')
    if type(transfer['file_mode']) is not int or not 0<=transfer['file_mode']<=0o777:
        raise HarnessError('Explicit handoff file_mode required')
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
            raise HarnessError('Mutable state root overlaps a process configuration')
        process = read_json(process_path)
        from .modules.goal_config.domain import GoalTypeDefinition
        process = GoalTypeDefinition.parse(process).data
        if process['goal_type'] != kind:
            raise HarnessError(f'Неоднозначный process {kind}')
        processes[kind] = process
    for entry in cfg['automatic_checks']:
        exact_keys(entry, {'paths','by_stage'}, 'automatic checks')
    return root, cfg, processes
