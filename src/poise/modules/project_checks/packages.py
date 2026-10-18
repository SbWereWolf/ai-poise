"""Declared general package boundaries, dependency order and inventory policy."""
from collections import defaultdict
from copy import deepcopy

from ..foundation.paths import matches_component_path
from .contracts import CheckFailure, paths, require, text


_LISTS = ('owners', 'dependencies', 'source_paths', 'fixture_paths',
          'triggering_input_changes', 'triggering_output_changes',
          'triggering_input_paths', 'triggering_output_paths',
          'shared_libraries', 'shared_databases', 'shared_library_paths', 'shared_database_paths')
_RELATIONS = (('source', 'source_paths'), ('test', None), ('fixture', 'fixture_paths'),
              ('input', 'triggering_input_paths'), ('output', 'triggering_output_paths'),
              ('shared_library', 'shared_library_paths'), ('shared_database', 'shared_database_paths'))


def package_error(condition, field, cause, recovery=None):
    require(condition, field, cause, code='invalid_packages', recovery=recovery)


class GeneralPackageCatalogue:
    def __init__(self, raw, driver_ids):
        package_error(isinstance(raw, dict) and type(raw.get('schema_version')) is int
                      and raw['schema_version'] == 1, 'packages.schema_version',
                      'Требуется версия 1 каталога пакетов.')
        package_error(isinstance(raw.get('packages'), list), 'packages.packages',
                      'Требуется список пакетов.')
        self.packages, self.by_id = [], {}
        for index, entry in enumerate(raw['packages']):
            where = f'packages.packages[{index}]'
            package_error(isinstance(entry, dict), where, 'Требуется таблица пакета.')
            value = {}
            for name in ('package_id', 'kind', 'description', 'contract'):
                value[name] = text(entry.get(name), where + '.' + name,
                                   identifier=name == 'package_id', code='invalid_packages')
            package_error(value['kind'] in ('unit', 'component', 'integration', 'acceptance'),
                          where + '.kind', 'Неизвестный вид пакета.')
            for name in _LISTS:
                if name not in entry:
                    raise CheckFailure('invalid_packages', where + '.' + name,
                                       f'Обязательное поле {name} отсутствует.',
                                       f'Укажите явный список {name} для {where}, включая пустой список при отсутствии таких входов.')
                values = entry[name]
                package_error(isinstance(values, list), where + '.' + name, 'Требуется список.')
                for pos, item in enumerate(values):
                    text(item, f'{where}.{name}[{pos}]', code='invalid_packages')
                package_error(len(set(values)) == len(values), where + '.' + name,
                              'Значения должны быть уникальными.')
                if name.endswith('_paths'):
                    paths(values, where + '.' + name, pattern=True, code='invalid_packages')
                value[name] = deepcopy(values)
            package_error(bool(value['owners']), where + '.owners', 'Укажите владельца пакета.')
            package_error(type(entry.get('bidirectional')) is bool, where + '.bidirectional',
                          'Требуется boolean.')
            value['bidirectional'] = entry['bidirectional']
            for side in ('producer', 'consumer'):
                if side in entry:
                    text(entry[side], where + '.' + side, code='invalid_packages')
                value[side] = entry.get(side)
            if value['kind'] == 'integration':
                package_error(bool(value['producer']) and bool(value['consumer']), where,
                              'Интеграционный пакет требует producer и consumer.')
            package_error(isinstance(entry.get('steps'), list) and bool(entry['steps']),
                          where + '.steps', 'Укажите шаги пакета.')
            value['steps'] = []
            for ordinal, step in enumerate(entry['steps']):
                field = f'{where}.steps[{ordinal}]'
                package_error(isinstance(step, dict), field, 'Требуется таблица шага.')
                driver = text(step.get('driver'), field + '.driver', code='invalid_packages')
                package_error(driver in driver_ids, field + '.driver', f'Драйвер {driver} не объявлен.')
                declared = paths(step.get('test_paths'), field + '.test_paths',
                                 pattern=True, code='invalid_packages')
                args = step.get('arguments')
                package_error(isinstance(args, list), field + '.arguments', 'Укажите список аргументов.')
                for pos, arg in enumerate(args):
                    package_error(isinstance(arg, str) and '\x00' not in arg,
                                  f'{field}.arguments[{pos}]', 'Требуется строковый аргумент.')
                value['steps'].append({'driver': driver, 'test_paths': deepcopy(declared),
                                       'arguments': deepcopy(args)})
            package_error(value['package_id'] not in self.by_id, where + '.package_id',
                          'Идентификатор пакета повторяется.')
            self.by_id[value['package_id']] = value
            self.packages.append(value)
        inventory = raw.get('inventory')
        package_error(isinstance(inventory, dict), 'packages.inventory', 'Укажите инвентаризацию тестов.')
        self.candidate_globs = paths(inventory.get('candidate_globs'),
                                     'packages.inventory.candidate_globs', pattern=True, code='invalid_packages')
        self.ignored_paths = paths(inventory.get('ignored_paths'),
                                  'packages.inventory.ignored_paths', pattern=True, code='invalid_packages')
        self.shared_tests = deepcopy(raw.get('shared_tests'))
        package_error(isinstance(self.shared_tests, list), 'packages.shared_tests', 'Требуется список.')
        seen = set()
        for i, shared in enumerate(self.shared_tests):
            where = f'packages.shared_tests[{i}]'
            package_error(isinstance(shared, dict), where, 'Требуется объявление общего теста.')
            paths([shared.get('path')], where + '.path', code='invalid_packages')
            text(shared.get('reason'), where + '.reason', code='invalid_packages')
            members = shared.get('packages')
            package_error(isinstance(members, list) and len(members) >= 2
                          and all(isinstance(v, str) and v in self.by_id for v in members)
                          and len(set(members)) == len(members), where + '.packages',
                          'Укажите точный набор не менее двух владельцев общего теста.')
            package_error(shared['path'] not in seen, where + '.path', 'Повторное объявление общего теста.')
            seen.add(shared['path'])
        for value in self.packages:
            self.order(value['package_id'])

    def get(self, package_id, field):
        if package_id not in self.by_id:
            raise CheckFailure('selection_missing', field, f'Пакет {package_id} не найден.',
                               'Выберите package_id из выбранного каталога пакетов.')
        return self.by_id[package_id]

    def order(self, package_id):
        visiting, visited, ordered = set(), set(), []

        def visit(identity):
            package_error(identity in self.by_id, 'packages.dependencies',
                          f'Зависимость {identity} не объявлена.')
            package_error(identity not in visiting, 'packages.dependencies',
                          f'Обнаружен цикл через пакет {identity}.')
            if identity in visited:
                return
            visiting.add(identity)
            for dependency in self.by_id[identity]['dependencies']:
                visit(dependency)
            visiting.remove(identity)
            visited.add(identity)
            ordered.append(self.by_id[identity])

        visit(package_id)
        return ordered

    def validate_inventory(self, actual_test_paths, matched_step_paths):
        """Adapters enumerate real files; the domain checks exact ownership."""
        candidates = {path for path in actual_test_paths
                      if not any(matches_component_path(path, rule) for rule in self.ignored_paths)}
        membership = defaultdict(set)
        for package_id, paths_for_steps in matched_step_paths.items():
            for path in paths_for_steps:
                membership[path].add(package_id)
        orphans = candidates - membership.keys()
        package_error(not orphans, 'packages.inventory',
                      'Тесты без владельца: ' + ', '.join(sorted(orphans)))
        shared = {v['path']: set(v['packages']) for v in self.shared_tests}
        for path, owners in membership.items():
            if len(owners) > 1:
                package_error(shared.get(path) == owners, 'packages.shared_tests',
                              f'Общий тест {path} требует точного набора владельцев {sorted(owners)}.')
        for path, owners in shared.items():
            package_error(membership.get(path) == owners, 'packages.shared_tests',
                          f'Объявление общего теста {path} не соответствует фактическим владельцам.')

    def impact(self, changed_paths):
        selected, mapped = [], set()
        for package in self.packages:
            matches = []
            for path in changed_paths:
                for relation, key in _RELATIONS:
                    patterns = ([p for step in package['steps'] for p in step['test_paths']]
                                if key is None else package[key])
                    for pattern in patterns:
                        if matches_component_path(path, pattern):
                            row = {'path': path, 'relation': relation, 'pattern': pattern}
                            if row not in matches:
                                matches.append(row)
                            mapped.add(path)
            if matches:
                selected.append({'package': deepcopy(package), 'matches': matches})
        return {'kind': 'package_impact', 'changed_paths': list(changed_paths),
                'packages': selected, 'unmapped_paths': [v for v in changed_paths if v not in mapped]}
