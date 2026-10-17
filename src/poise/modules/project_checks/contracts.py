"""Strict batch admission and actionable failures; no I/O or execution."""
from copy import deepcopy
from dataclasses import dataclass
import math
import re


class CheckFailure(ValueError):
    def __init__(self, code, field, cause, recovery, *, reason=None, status='invalid'):
        self.code, self.field, self.status = code, field, status
        self.cause, self.recovery = cause, recovery
        self.reason = reason if reason is not None else f'{field}: {cause}'
        super().__init__(self.reason)

    def as_dict(self, references=None):
        return {'code': self.code, 'field': self.field, 'cause': self.cause,
                'reason': self.reason, 'recovery': self.recovery,
                'references': deepcopy(references) if references is not None else {
                    'response_path': None, 'record_paths': [],
                    'stdout_paths': [], 'stderr_paths': []}}


def require(condition, field, cause, *, code='invalid_request', recovery=None):
    if not condition:
        raise CheckFailure(code, field, cause,
                           recovery if recovery is not None else f'Исправьте {field}: {cause}')


def exact(value, required, field, optional=(), *, code='invalid_request'):
    require(isinstance(value, dict), field, 'Требуется объект.', code=code)
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    require(not missing, field, 'Отсутствуют поля: ' + ', '.join(sorted(missing)), code=code)
    require(not extra, field, 'Неизвестные поля: ' + ', '.join(sorted(extra)), code=code)


def text(value, field, *, identifier=False, code='invalid_request'):
    require(isinstance(value, str) and bool(value.strip()) and '\x00' not in value,
            field, 'Требуется непустая строка.', code=code)
    if identifier:
        require(re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', value) is not None,
                field, 'Требуется идентификатор длиной до 96 символов.', code=code)
    return value


def number(value, field, *, integer=False, zero=False, code='invalid_request'):
    valid_type = type(value) is int if integer else type(value) in (int, float)
    if not valid_type and isinstance(value, bool):
        raise CheckFailure(code, field, 'Получен boolean вместо положительного числа.',
                           f'Укажите положительное числовое значение {field}.',
                           reason=f'{field} должен быть положительным числом; получен boolean.')
    require(valid_type and math.isfinite(value), field, 'Требуется конечное число.', code=code)
    if not zero and value <= 0:
        raise CheckFailure(code, field, f'Значение {value} не превышает нижнюю границу 0.',
                           f'Укажите {field} больше 0.',
                           reason=f'{field} должен быть больше 0; получено {value}.')
    require(value >= 0, field, 'Требуется неотрицательное число.', code=code)
    return value


def relative_path(value, field, *, pattern=False, code='invalid_options', concrete_change=False):
    text(value, field, code=code)
    parts = value.split('/')
    if '..' in parts:
        instruction = 'канонический относительный путь' if concrete_change else 'путь'
        raise CheckFailure(code, field, f'Путь {value} выходит за корень выбранной рабочей копии.',
                           f'Передайте {instruction} внутри выбранной рабочей копии без сегмента .. в {field}.',
                           reason=f'{field} должен быть каноническим относительным путём внутри рабочей копии.')
    if value.startswith('/'):
        raise CheckFailure(code, field,
                           f'Абсолютный путь {value} не является путём внутри выбранной рабочей копии.',
                           f'Передайте относительный путь в {field}.')
    if not pattern and any(c in value for c in '*?[]'):
        raise CheckFailure(code, field,
                           f'Изменённый путь {value} содержит шаблон вместо конкретного пути.',
                           f'Передайте конкретный изменённый путь в {field}.')
    require(not value.startswith('/') and not any(c in value for c in '\\:')
            and all(ord(c) >= 32 and ord(c) != 127 for c in value)
            and all(p not in ('', '.') for p in parts)
            and (pattern or not any(c in value for c in '*?[]')),
            field, f'Путь {value} должен быть каноническим относительным путём.', code=code)
    return value


def paths(values, field, *, nullable=False, pattern=False, code='invalid_options', concrete_change=False):
    if values is None and nullable:
        return None
    require(isinstance(values, list), field, 'Требуется список путей.', code=code)
    for i, value in enumerate(values):
        relative_path(value, f'{field}[{i}]', pattern=pattern, code=code, concrete_change=concrete_change)
    require(len(set(values)) == len(values), field, 'Пути должны быть уникальными.', code=code)
    return values


def selection(value, field):
    require(isinstance(value, dict), field, 'Требуется выбор проверки.')
    kind = value.get('kind')
    if kind == 'profile':
        exact(value, ('kind', 'id', 'workload_class', 'runtime_class'), field)
        text(value['id'], field + '.id', identifier=True)
        for axis in ('workload_class', 'runtime_class'):
            if value[axis] is not None:
                text(value[axis], field + '.' + axis, identifier=True)
    elif kind in ('package', 'suite'):
        exact(value, ('kind', 'id'), field)
        text(value['id'], field + '.id', identifier=True)
    elif kind == 'suite_set':
        exact(value, ('kind', 'profile', 'changed_paths'), field)
        profile = value['profile']
        if profile not in ('fast', 'release'):
            raise CheckFailure('invalid_request', field + '.profile',
                               f'Профиль {profile} не поддерживается для выбора наборов.',
                               f'Укажите fast или release в {field}.profile.',
                               reason=f'{field}.profile должен быть fast или release; получено {profile}.')
        paths(value['changed_paths'], field + '.changed_paths', nullable=True)
    else:
        require(False, field + '.kind', 'Неизвестный вид проверки.')
    return value


def options(value, field):
    exact(value, ('fresh', 'scope_paths', 'timeout_override'), field, ('documentation_scope',))
    if type(value['fresh']) is not bool:
        if isinstance(value['fresh'], str):
            raise CheckFailure('invalid_request', field + '.fresh',
                               'Получена строка вместо boolean.',
                               f'Передайте true или false в {field}.fresh.',
                               reason=f'{field}.fresh должен быть boolean; получена строка.')
        require(False, field + '.fresh', 'Требуется boolean.')
    paths(value['scope_paths'], field + '.scope_paths', nullable=True)
    override = value['timeout_override']
    if override is not None:
        where = field + '.timeout_override'
        exact(override, ('seconds', 'evidence_run_id', 'reason'), where)
        number(override['seconds'], where + '.seconds', code='invalid_options')
        text(override['evidence_run_id'], where + '.evidence_run_id')
        text(override['reason'], where + '.reason', code='override_invalid')
    # Subject admission owns the distinction between ABSENT, null and malformed.
    return value


@dataclass(frozen=True)
class CheckRequest:
    operation: str
    items: tuple

    @classmethod
    def parse(cls, value, max_items):
        exact(value, ('schema', 'operation', 'items'), 'request')
        require(value['schema'] == 'poise-project-checks-request-1', 'request.schema',
                'Неподдерживаемая версия запроса.')
        operation, items = value['operation'], value['items']
        require(isinstance(operation, str) and operation in
                ('list', 'describe', 'preview', 'run', 'result', 'calibrate', 'package_impact'),
                'request.operation', 'Неизвестная операция.')
        require(isinstance(items, list) and 0 < len(items) <= max_items,
                'request.items', f'Требуется от 1 до {max_items} элементов.')
        admitted = []
        for i, item in enumerate(items):
            where = f'request.items[{i}]'
            require(item not in admitted, where, 'Повторяющийся элемент запроса.')
            if operation in ('run', 'preview'):
                exact(item, ('selection', 'options'), where)
                selection(item['selection'], where + '.selection')
                options(item['options'], where + '.options')
            elif operation == 'describe':
                selection(item, where)
            elif operation == 'list':
                exact(item, ('kind', 'platform'), where)
                require(item['kind'] in ('profiles', 'packages', 'suites'), where + '.kind',
                        'Неизвестный каталог.')
                require(item['platform'] in (None, 'linux', 'darwin', 'windows'),
                        where + '.platform', 'Неизвестная платформа.')
            elif operation == 'result':
                exact(item, ('result_id',), where)
                text(item['result_id'], where + '.result_id')
            elif operation == 'package_impact':
                exact(item, ('changed_paths',), where)
                paths(item['changed_paths'], where + '.changed_paths', concrete_change=True)
            else:
                exact(item, ('profile', 'evidence_run_ids', 'expected_profile_sha256'), where)
                selection(item['profile'], where + '.profile')
                require(item['profile']['kind'] == 'profile', where + '.profile',
                        'Калибровка требует профиль.')
                ids = item['evidence_run_ids']
                require(isinstance(ids, list) and bool(ids), where + '.evidence_run_ids',
                        'Укажите доказательства выполненных запусков.')
                for index, identity in enumerate(ids):
                    text(identity, f'{where}.evidence_run_ids[{index}]')
                require(len(set(ids)) == len(ids), where + '.evidence_run_ids',
                        'Идентификаторы должны быть уникальными.')
                require(isinstance(item['expected_profile_sha256'], str) and
                        re.fullmatch('[0-9a-f]{64}', item['expected_profile_sha256']) is not None,
                        where + '.expected_profile_sha256', 'Требуется SHA256 выбранного файла.')
            admitted.append(deepcopy(item))
        return cls(operation, tuple(admitted))
