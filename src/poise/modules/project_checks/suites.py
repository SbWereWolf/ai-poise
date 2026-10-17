"""Explicit suite profile membership and declared source-area selection."""
from copy import deepcopy

from .contracts import CheckFailure, paths, require, selection, text


def source_matches(path, source):
    return path == source or path.startswith(source + '/')


class SuiteCatalogue:
    def __init__(self, raw):
        require(isinstance(raw, dict) and type(raw.get('schema_version')) is int
                and raw['schema_version'] == 1, 'suites.schema_version',
                'Требуется версия 1 каталога наборов.', code='invalid_suites')
        require(isinstance(raw.get('suites'), list), 'suites.suites',
                'Требуется список наборов.', code='invalid_suites')
        self.suites, self.by_id = [], {}
        for i, item in enumerate(raw['suites']):
            where = f'suites.suites[{i}]'
            require(isinstance(item, dict), where, 'Требуется объект набора.', code='invalid_suites')
            identity = text(item.get('id'), where + '.id', identifier=True, code='invalid_suites')
            require(identity not in self.by_id, where + '.id', 'Повторяющийся набор.', code='invalid_suites')
            profiles = item.get('profiles')
            require(isinstance(profiles, list) and bool(profiles)
                    and all(isinstance(v, str) and v in ('fast', 'release') for v in profiles)
                    and len(set(profiles)) == len(profiles), where + '.profiles',
                    'Укажите уникальные fast/release memberships.', code='invalid_suites')
            sources = paths(item.get('sources'), where + '.sources', code='invalid_suites')
            commands = item.get('commands')
            require(isinstance(commands, list) and bool(commands), where + '.commands',
                    'Укажите команды набора.', code='invalid_suites')
            for index, command in enumerate(commands):
                try:
                    selection(command, f'{where}.commands[{index}]')
                except CheckFailure as exc:
                    raise CheckFailure('invalid_suites', exc.field, exc.cause, exc.recovery) from exc
                require(command['kind'] in ('profile', 'package'), f'{where}.commands[{index}].kind',
                        'Набор содержит профили или пакеты.', code='invalid_suites')
            known = {'id': identity, 'profiles': list(profiles),
                     'sources': deepcopy(sources), 'commands': deepcopy(commands)}
            self.suites.append(known)
            self.by_id[identity] = known

    def get(self, suite_id, field):
        if suite_id not in self.by_id:
            raise CheckFailure('selection_missing', field, f'Набор {suite_id} не найден.',
                               'Выберите id из выбранного каталога наборов.')
        return self.by_id[suite_id]

    def select(self, profile, changed_paths):
        members = [v for v in self.suites if profile in v['profiles']]
        if changed_paths is None:
            return members, [], [], 'all_profile_members'
        if not changed_paths:
            return [], [], [], 'empty_change_set'
        selected, matches, mapped = [], [], set()
        for suite in members:
            sources = [s for s in suite['sources'] if any(source_matches(p, s) for p in changed_paths)]
            paths_for_suite = [p for p in changed_paths if any(source_matches(p, s) for s in sources)]
            if paths_for_suite:
                selected.append(suite)
                mapped.update(paths_for_suite)
                matches.append({'suite_id': suite['id'], 'sources': sources,
                                'changed_paths': paths_for_suite})
        return (selected, matches, [p for p in changed_paths if p not in mapped],
                'matching_sources' if selected else 'no_matching_source')
