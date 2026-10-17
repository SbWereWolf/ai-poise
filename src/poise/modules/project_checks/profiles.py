"""Typed known profile values with raw configuration retained by its I/O owner."""
from copy import deepcopy
from datetime import datetime
import re

from .contracts import CheckFailure, number, paths, require, text


PARSERS = ('generic', 'phpunit', 'vitest', 'playwright', 'phpstan', 'deptrac', 'build')


def valid(condition, field, cause, recovery=None):
    require(condition, field, cause, code='invalid_profiles', recovery=recovery)


def string_list(value, field, *, empty=True):
    valid(isinstance(value, list) and (bool(value) or empty), field, 'Требуется список строк.')
    for i, item in enumerate(value):
        text(item, f'{field}[{i}]', code='invalid_profiles')
    return deepcopy(value)


def profile_from_raw(raw, index):
    where = f'profiles.commands[{index}]'
    valid(isinstance(raw, dict), where, 'Требуется таблица профиля.')
    value = {}
    for key in ('command_id', 'workload_class', 'runtime_class', 'invocation', 'cwd',
                'parser', 'initial_limit_reason', 'last_calibrated_at'):
        value[key] = text(raw.get(key), where + '.' + key,
                          identifier=key in ('command_id', 'workload_class', 'runtime_class'),
                          code='invalid_profiles')
    argv = raw.get('argv')
    if argv == []:
        raise CheckFailure('invalid_profiles', where + '.argv', 'Список аргументов пуст.',
                           f'Укажите непустой список аргументов {where}.argv.',
                           reason=f'{where}.argv должен содержать исполняемую команду.')
    valid(isinstance(argv, list) and bool(argv), where + '.argv', 'Укажите список аргументов.')
    valid(all(isinstance(v, str) and '\x00' not in v for v in argv) and bool(argv[0]),
          where + '.argv', 'Исполняемая команда и аргументы должны быть строками без NUL.')
    value['argv'] = list(argv)
    value['platforms'] = string_list(raw.get('platforms'), where + '.platforms', empty=False)
    valid(all(v in ('linux', 'darwin', 'windows') for v in value['platforms']),
          where + '.platforms', 'Неизвестная платформа.')
    valid(value['invocation'] in ('exec', 'windows_cmd'), where + '.invocation', 'Неизвестный способ запуска.')
    if value['parser'] not in PARSERS:
        parser = value['parser']
        raise CheckFailure('invalid_profiles', where + '.parser', f'Парсер {parser} не поддерживается.',
                           f'Укажите generic, phpunit, vitest, playwright, phpstan, deptrac или build в {where}.parser.',
                           reason=f'{where}.parser содержит неподдерживаемый парсер {parser}.')
    if value['cwd'] != '.':
        paths([value['cwd']], where + '.cwd', code='invalid_profiles')
    valid(type(raw.get('require_parser_confirmation')) is bool,
          where + '.require_parser_confirmation', 'Требуется boolean.')
    value['require_parser_confirmation'] = raw['require_parser_confirmation']
    for key, zero, integer in (('execution_timeout_seconds', False, True),
                                ('observed_max_success_seconds', True, False),
                                ('observed_max_progress_gap_seconds', True, False),
                                ('stall_timeout_seconds', True, True)):
        value[key] = number(raw.get(key), where + '.' + key, zero=zero, integer=integer,
                            code='invalid_profiles')
    try:
        calibrated = datetime.fromisoformat(value['last_calibrated_at'].replace('Z', '+00:00'))
        valid(calibrated.tzinfo is not None and calibrated.utcoffset() is not None,
              where + '.last_calibrated_at', 'Требуется временная зона.')
    except ValueError as exc:
        if isinstance(exc, CheckFailure):
            raise
        raise CheckFailure('invalid_profiles', where + '.last_calibrated_at',
                           'Неверный ISO timestamp.', 'Укажите дату, время и временную зону.') from exc
    progress = raw.get('progress')
    valid(isinstance(progress, dict), where + '.progress', 'Укажите настройку прогресса.')
    mode = progress.get('mode')
    valid(mode in ('regex', 'unavailable'), where + '.progress.mode', 'Неизвестный режим прогресса.')
    known_progress = {'mode': mode}
    if mode == 'regex':
        for key in ('pattern', 'completed_group'):
            known_progress[key] = text(progress.get(key), where + '.progress.' + key, code='invalid_profiles')
        for key in ('total_group', 'stage_group'):
            if key in progress:
                known_progress[key] = text(progress[key], where + '.progress.' + key, code='invalid_profiles')
        try:
            compiled = re.compile(known_progress['pattern'])
        except re.error as exc:
            raise CheckFailure('invalid_profiles', where + '.progress.pattern', str(exc),
                               'Исправьте регулярное выражение прогресса.') from exc
        for key in ('completed_group', 'total_group', 'stage_group'):
            if key in known_progress:
                valid(known_progress[key] in compiled.groupindex, where + '.progress.' + key,
                      'Именованная группа отсутствует в pattern.')
    else:
        valid(not any(k in progress for k in ('pattern', 'completed_group', 'total_group', 'stage_group')),
              where + '.progress', 'Режим unavailable не принимает regex-поля.')
        valid(value['stall_timeout_seconds'] == 0 and value['observed_max_progress_gap_seconds'] == 0,
              where + '.stall_timeout_seconds', 'Контроль простоя требует наблюдаемого прогресса.')
    value['progress'] = known_progress
    output = raw.get('output')
    valid(isinstance(output, dict), where + '.output', 'Укажите настройки вывода.')
    value['output'] = {k: number(output.get(k), where + '.output.' + k, integer=True,
                                 code='invalid_profiles')
                       for k in ('success_bytes', 'failure_bytes', 'timeout_bytes', 'max_failures')}
    prerequisites = raw.get('prerequisites')
    valid(isinstance(prerequisites, dict), where + '.prerequisites', 'Укажите prerequisites.')
    value['prerequisites'] = {k: string_list(prerequisites.get(k), where + '.prerequisites.' + k)
                              for k in ('executables', 'files', 'environment')}
    paths(value['prerequisites']['files'], where + '.prerequisites.files', code='invalid_profiles')
    cache = raw.get('cache')
    valid(isinstance(cache, dict), where + '.cache', 'Укажите cache.')
    valid(type(cache.get('enabled')) is bool, where + '.cache.enabled', 'Требуется boolean.')
    selection = cache.get('input_selection')
    valid(selection in ('filesystem-glob', 'git-tracked-markdown'), where + '.cache.input_selection',
          'Явно укажите filesystem-glob или git-tracked-markdown.')
    known_cache = {'enabled': cache['enabled'], 'input_selection': selection,
                   'input_paths': paths(cache.get('input_paths'), where + '.cache.input_paths',
                                        pattern=True, code='invalid_profiles'),
                   'environment': string_list(cache.get('environment'), where + '.cache.environment')}
    if selection == 'git-tracked-markdown':
        valid(not known_cache['input_paths'], where + '.cache.input_paths',
              'Выбор tracked Markdown требует пустого списка input_paths.')
        for key in ('max_input_files', 'max_input_bytes'):
            known_cache[key] = number(cache.get(key), where + '.cache.' + key,
                                      integer=True, code='invalid_profiles')
    else:
        valid(not any(k in cache for k in ('max_input_files', 'max_input_bytes')),
              where + '.cache', 'Пределы входов относятся к tracked Markdown.')
    value['cache'] = known_cache
    if 'evidence_id' in raw:
        value['evidence_id'] = text(raw['evidence_id'], where + '.evidence_id', identifier=True,
                                    code='invalid_profiles')
    if 'subject' in raw:
        subject = raw['subject']
        valid(isinstance(subject, dict) and subject.get('kind') == 'documentation'
              and type(subject.get('version')) is int and subject['version'] == 1
              and subject.get('mode') in ('working', 'committed'),
              where + '.subject', 'Укажите documentation subject версии 1 с явным режимом.')
        mode_arg = text(subject.get('mode_argument'), where + '.subject.mode_argument', code='invalid_profiles')
        environment_key = text(subject.get('environment_key'), where + '.subject.environment_key', code='invalid_profiles')
        valid(not known_cache['enabled'], where + '.cache.enabled',
              'Проверка документации требует отключённого повторного использования.')
        valid(argv.count(mode_arg) == 1 and argv.index(mode_arg) + 1 < len(argv)
              and argv[argv.index(mode_arg) + 1] == subject['mode'], where + '.argv',
              'Аргументы должны явно выбирать настроенный режим документации.')
        value['subject'] = {'kind': 'documentation', 'version': 1, 'mode': subject['mode'],
                            'mode_argument': mode_arg, 'environment_key': environment_key}
    return value


class ProfileCatalogue:
    def __init__(self, raw):
        valid(isinstance(raw, dict) and type(raw.get('schema_version')) is int
              and raw['schema_version'] == 1, 'profiles.schema_version', 'Требуется версия 1.')
        valid(isinstance(raw.get('commands'), list) and bool(raw['commands']),
              'profiles.commands', 'Укажите профили команд.')
        self.profiles = [profile_from_raw(v, i) for i, v in enumerate(raw['commands'])]
        keys = [(v['command_id'], v['workload_class'], v['runtime_class']) for v in self.profiles]
        valid(len(set(keys)) == len(keys), 'profiles.commands', 'Повторяющийся ключ профиля.')
        declared = raw.get('evidence_contracts')
        valid(isinstance(declared, list), 'profiles.evidence_contracts', 'Укажите список evidence contracts.')
        self.evidence = {}
        for i, entry in enumerate(declared):
            where = f'profiles.evidence_contracts[{i}]'
            valid(isinstance(entry, dict), where, 'Требуется таблица evidence contract.')
            identity = text(entry.get('evidence_id'), where + '.evidence_id', identifier=True, code='invalid_profiles')
            valid(identity not in self.evidence, where + '.evidence_id', 'Повторяющийся evidence contract.')
            known = {'evidence_id': identity}
            for key in ('required_globs', 'optional_globs'):
                known[key] = paths(entry.get(key), where + '.' + key, pattern=True, code='invalid_profiles')
            valid(not set(known['required_globs']) & set(known['optional_globs']), where,
                  'Обязательные и необязательные шаблоны должны различаться.')
            for key in ('max_files', 'max_total_bytes'):
                known[key] = number(entry.get(key), where + '.' + key, integer=True, code='invalid_profiles')
            valid(entry.get('retention_class') in ('task', 'release', 'diagnostic'),
                  where + '.retention_class', 'Неизвестный retention class.')
            known['retention_class'] = entry['retention_class']
            self.evidence[identity] = known
        for i, profile in enumerate(self.profiles):
            if 'evidence_id' in profile:
                valid(profile['evidence_id'] in self.evidence, f'profiles.commands[{i}].evidence_id',
                      'Evidence contract не объявлен.')

    def select(self, selected, field, warnings):
        candidates = [v for v in self.profiles if v['command_id'] == selected['id']]
        if not candidates:
            raise CheckFailure('selection_missing', field + '.id', f'Профиль {selected["id"]} не найден.',
                               'Выберите команду из выбранного каталога профилей.')
        for axis in ('workload_class', 'runtime_class'):
            chosen = selected[axis]
            if chosen is not None:
                matching = [v for v in candidates if v[axis] == chosen]
                if not matching:
                    warnings.append(f'{field}.{axis}: неизвестное необязательное значение {chosen}; выбор продолжается.')
                else:
                    candidates = matching
        if len(candidates) != 1:
            axis = 'workload_class' if len({v['workload_class'] for v in candidates}) > 1 else 'runtime_class'
            values = list(dict.fromkeys(v[axis] for v in candidates))
            variants = ' и '.join(v['command_id'] + '/' + v['workload_class'] + '/' + v['platforms'][0]
                                  for v in candidates)
            name = selected['id']
            raise CheckFailure('selection_ambiguous', field + '.' + axis,
                               f'Профиль {name} соответствует кандидатам {variants}.',
                               f'Укажите {axis} ' + ' или '.join(values) + f' в {field}.{axis}.',
                               reason=f'Выбор профиля {name} неоднозначен по {axis}.')
        return candidates[0]


def resolved_selection(profile):
    return {'kind': 'profile', 'id': profile['command_id'],
            'workload_class': profile['workload_class'], 'runtime_class': profile['runtime_class']}
