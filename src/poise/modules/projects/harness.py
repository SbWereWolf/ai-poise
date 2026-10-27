"""General project configuration, independent of Task workflow contracts."""
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import PurePath

from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields, nonempty
from .resources import validate_resources


def positive(value, label, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            or (integer and type(value) is not int)):
        raise PoiseError(f'general_config_invalid: {label} requires a positive '
                         + ('integer' if integer else 'finite number'))


@dataclass(frozen=True)
class TaskAdapterReference:
    config_path: str

    @classmethod
    def parse(cls, raw):
        if raw is None:
            return None
        values = configuration_fields(raw, {'schema', 'config_path'}, 'task_adapter')
        if values['schema'] != 'stride-task-adapter-1':
            raise PoiseError('general_config_invalid: unsupported task_adapter.schema')
        nonempty(values['config_path'], 'task_adapter.config_path')
        if not PurePath(values['config_path']).is_absolute():
            raise PoiseError('general_config_invalid: task_adapter.config_path must be absolute')
        return cls(values['config_path'])


@dataclass(frozen=True)
class HarnessProject:
    raw: dict
    known: dict
    task_adapter: TaskAdapterReference | None

    @classmethod
    def parse(cls, raw):
        known = configuration_fields(raw, {'schema', 'project', 'git', 'paths', 'limits',
                                          'batch', 'runtime_resources', 'task_adapter'},
                                     'general project')
        if known['schema'] != 'poise-harness-project-1':
            raise PoiseError('unsupported_general_schema: select poise-harness-project-1')
        nonempty(known['project'], 'project')
        git = configuration_fields(known['git'], {'repository'}, 'git')
        nonempty(git['repository'], 'git.repository')
        if not PurePath(git['repository']).is_absolute():
            raise PoiseError('general_config_invalid: git.repository must be absolute')
        paths = configuration_fields(known['paths'], {'state', 'database', 'lock', 'runtime',
                                                     'runs', 'stdout', 'stderr', 'response'}, 'paths')
        for key, value in paths.items():
            nonempty(value, f'paths.{key}')
            path = PurePath(value)
            if key != 'state' and (path.is_absolute() or '..' in path.parts or not path.parts):
                raise PoiseError(f'general_config_invalid: paths.{key} must remain within state')
        if paths['database'] == paths['lock']:
            raise PoiseError('general_config_invalid: identity database and lock must differ')
        limits = configuration_fields(known['limits'], {'git_seconds', 'lock_seconds',
            'lock_poll_seconds', 'sqlite_read_seconds', 'output_chars'}, 'limits')
        for key, value in limits.items():
            positive(value, f'limits.{key}', integer=key == 'output_chars')
        batch = configuration_fields(known['batch'], {'max_items', 'max_input_bytes',
                                                     'file_mode', 'message_source'}, 'batch')
        for key in ('max_items', 'max_input_bytes'):
            positive(batch[key], f'batch.{key}', integer=True)
        if type(batch['file_mode']) is not int or not 0 <= batch['file_mode'] <= 0o777:
            raise PoiseError('general_config_invalid: explicit POSIX batch.file_mode required')
        source = configuration_fields(batch['message_source'], {'id', 'mode'}, 'message_source')
        nonempty(source['id'], 'message_source.id')
        if source['mode'] not in ('runtime_event', 'reported', 'transcript'):
            raise PoiseError('general_config_invalid: unsupported message_source.mode')
        validate_resources(known['runtime_resources'])
        return cls(deepcopy(raw), deepcopy(known), TaskAdapterReference.parse(known['task_adapter']))
