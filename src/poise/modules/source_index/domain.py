"""Source-index admission and truthful outcomes, independent of I/O."""
from dataclasses import dataclass
import math

from ..foundation.errors import PoiseError
from ..foundation.validation import nonempty, validate_exact_keys


class IndexFailure(PoiseError):
    def __init__(self, status, reason, recovery):
        self.status, self.reason, self.recovery = status, reason, recovery
        super().__init__(reason)


def require(condition, field, explanation):
    if not condition:
        raise IndexFailure('rejected', f'{field}: {explanation}',
                           f'Исправьте явно выбранное поле {field}: {explanation}')


def exact(value, fields, label):
    try:
        validate_exact_keys(value, set(fields), label, PoiseError)
    except PoiseError as exc:
        raise IndexFailure('rejected', str(exc), f'Исправьте поля {label}.') from exc


def strings(value, label, *, populated=False):
    require(isinstance(value, list) and (bool(value) or not populated), label,
            'требуется явный список строк')
    for item in value:
        try:
            nonempty(item, label)
        except PoiseError as exc:
            raise IndexFailure('rejected', str(exc), f'Исправьте {label}.') from exc
    return tuple(value)


@dataclass(frozen=True)
class IndexRequest:
    operation: str
    arguments: tuple[str, ...]

    @classmethod
    def parse(cls, value):
        exact(value, ('operation', 'arguments'), 'request')
        operation = value['operation']
        require(isinstance(operation, str) and operation in
                ('status', 'rebuild', 'update', 'query'), 'operation', 'неизвестная операция')
        arguments = strings(value['arguments'], 'arguments')
        require(bool(arguments) if operation == 'query' else not arguments,
                'arguments', 'аргументы требуются только для query')
        return cls(operation, arguments)


@dataclass(frozen=True)
class IndexPolicy:
    enabled: bool
    command_argv: tuple[str, ...]
    query_commands: tuple[str, ...]
    paths: dict
    limits: dict

    @classmethod
    def parse(cls, value):
        exact(value, ('schema', 'enabled', 'command_argv', 'query_commands', 'paths', 'limits'), 'policy')
        require(value['schema'] == 'poise-source-index-policy-1', 'schema', 'неподдерживаемая policy')
        require(type(value['enabled']) is bool, 'enabled', 'требуется boolean')
        command = strings(value['command_argv'], 'command_argv', populated=True)
        queries = strings(value['query_commands'], 'query_commands', populated=True)
        require(len(set(queries)) == len(queries), 'query_commands', 'подкоманды должны быть уникальны')
        require(all(not item.startswith('-') and item not in ('update', 'rebuild') for item in queries),
                'query_commands', 'укажите read-only подкоманды')
        paths, limits = value['paths'], value['limits']
        exact(paths, ('database', 'lock', 'runs'), 'paths')
        for key, path in paths.items():
            require(isinstance(path, str) and bool(path) and '\0' not in path and
                    not path.startswith('/') and '\\' not in path and
                    all(part not in ('', '.', '..') for part in path.split('/')),
                    key, 'containment: требуется относительный путь внутри рабочей копии')
        exact(limits, ('lock_seconds', 'lock_poll_seconds', 'run_seconds', 'cleanup_seconds'), 'limits')
        for key, number in limits.items():
            require(type(number) in (float, int) and math.isfinite(number) and number > 0,
                    key, 'требуется явное положительное конечное число')
        return cls(value['enabled'], command, queries, dict(paths), dict(limits))


@dataclass(frozen=True)
class IndexHealth:
    data: dict

    @classmethod
    def parse(cls, value, database, exists, recovery):
        stats = value.get('stats') if isinstance(value, dict) else None
        count = stats.get('file_count') if isinstance(stats, dict) else None
        if (not exists or not isinstance(value, dict) or value.get('db_path') != database
                or type(count) is not int or count < 0):
            raise IndexFailure('unavailable', 'index: состояние выбранной БД не подтверждено', recovery)
        return cls(value)


def execution_succeeded(result):
    terminal = result['termination']
    return (result['actual_exit_code'] == 0 and not result['timed_out'] and not result['cancelled']
            and result['capture_complete'] and terminal['child_reaped']
            and terminal['process_group_stopped'] and terminal['capture_complete'])


def exit_code(status):
    if status == 'rejected':
        return 2
    return 0 if status in ('ready', 'complete', 'disabled') else 1
