"""Explicit outside-Git policy/catalogue loading, preserving raw configuration."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import tomllib

from ...common import PoiseError
from ...modules.project_checks.contracts import CheckFailure, number, paths, require, text
from ...modules.project_checks.profiles import ProfileCatalogue
from ...modules.project_checks.packages import GeneralPackageCatalogue
from ...modules.project_checks.suites import SuiteCatalogue
from ..goal_config import strict_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def absolute(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    text(value, field, code='invalid_policy')
    require(Path(value).is_absolute(), field, 'Укажите абсолютный путь.', code='invalid_policy')
    return Path(value)


def read_raw(path, code, field, *, toml=False, unavailable=False):
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CheckFailure(code, field, f'Не удалось прочитать {path}: {exc}',
                           f'Укажите доступный файл {field}.',
                           status='unavailable' if unavailable else 'invalid') from exc
    try:
        value = tomllib.loads(data.decode('utf-8')) if toml else strict_json(data.decode('utf-8'))
        require(isinstance(value, dict), field, 'Требуется объект настройки.', code=code)
    except (PoiseError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CheckFailure):
            raise
        raise CheckFailure(code, field, f'Неверный формат файла {path}: {exc}',
                           f'Исправьте формат выбранного файла {field}.') from exc
    return value, digest(data)


@dataclass
class CheckConfiguration:
    bound: object
    policy_path: Path
    policy: dict
    policy_sha256: str
    profiles_raw: dict
    profiles_sha256: str
    profiles: ProfileCatalogue
    packages: GeneralPackageCatalogue | None
    suites: SuiteCatalogue | None
    drivers: dict
    state: Path

    @classmethod
    def load(cls, bound, policy_path):
        policy_path = absolute(str(policy_path), 'policy')
        policy, policy_sha = read_raw(policy_path, 'unavailable_policy', 'policy', unavailable=True)
        code = 'invalid_policy'
        require(policy.get('schema') == 'poise-project-checks-policy-1', 'policy.schema',
                'Неподдерживаемая версия настройки.', code=code)
        if 'profiles_path' not in policy:
            raise CheckFailure(code, 'policy.profiles_path', 'Обязательное поле отсутствует.',
                               'Укажите абсолютный путь policy.profiles_path к выбранному каталогу профилей.',
                               reason='В настройках отсутствует policy.profiles_path.')
        profiles_path = absolute(policy['profiles_path'], 'policy.profiles_path')
        for key in ('packages_path', 'suites_path'):
            require(key in policy, 'policy.' + key, 'Обязательное поле отсутствует.', code=code)
            absolute(policy[key], 'policy.' + key, nullable=True)
        storage = policy.get('storage')
        require(isinstance(storage, dict), 'policy.storage', 'Укажите размещение данных.', code=code)
        state = absolute(storage.get('directory'), 'policy.storage.directory')
        configured_state = Path(bound.config['paths']['state'])
        if not configured_state.is_absolute():
            configured_state = bound.root / configured_state
        require(state.resolve().is_relative_to(configured_state.resolve()), 'policy.storage.directory',
                'Каталог должен находиться внутри state выбранного проекта.', code=code)
        for key in ('ledger_file', 'lock_file', 'runs_directory', 'cache_directory', 'response_directory'):
            paths([storage.get(key)], 'policy.storage.' + key, code=code)
        timing = policy.get('timing')
        require(isinstance(timing, dict), 'policy.timing', 'Укажите политику длительности.', code=code)
        for key in ('history_multiplier', 'poll_seconds', 'cleanup_seconds', 'diagnostic_override_max_seconds'):
            number(timing.get(key), 'policy.timing.' + key, code=code)
        environment = policy.get('environment')
        require(isinstance(environment, dict) and isinstance(environment.get('clear_keys'), list),
                'policy.environment.clear_keys', 'Укажите список очищаемых переменных.', code=code)
        for i, key in enumerate(environment['clear_keys']):
            text(key, f'policy.environment.clear_keys[{i}]', code=code)
        publication = policy.get('publication')
        require(isinstance(publication, dict), 'policy.publication', 'Укажите размещение отчёта.', code=code)
        absolute(publication.get('report_directory'), 'policy.publication.report_directory')
        adapters = policy.get('runtime_adapters')
        require(isinstance(adapters, list), 'policy.runtime_adapters', 'Укажите адаптеры runtime.', code=code)
        adapter_ids = set()
        for i, adapter in enumerate(adapters):
            field = f'policy.runtime_adapters[{i}]'
            require(isinstance(adapter, dict), field, 'Требуется объект.', code=code)
            identity = text(adapter.get('id'), field + '.id', identifier=True, code=code)
            require(identity not in adapter_ids, field + '.id', 'Повторный runtime adapter.', code=code)
            adapter_ids.add(identity)
            platforms = adapter.get('platforms')
            require(isinstance(platforms, list) and bool(platforms)
                    and all(isinstance(v, str) and v in ('linux', 'darwin', 'windows') for v in platforms),
                    field + '.platforms', 'Укажите поддерживаемые платформы.', code=code)
            require(adapter.get('invocation') in ('exec', 'windows_cmd'), field + '.invocation',
                    'Укажите способ запуска.', code=code)
            require('command_processor' in adapter, field + '.command_processor',
                    'Обязательное поле отсутствует.', code=code)
            if adapter['invocation'] == 'windows_cmd':
                text(adapter['command_processor'], field + '.command_processor', code=code)
            else:
                require(adapter['command_processor'] is None, field + '.command_processor',
                        'Exec не использует command processor.', code=code)
        raw_drivers = policy.get('drivers')
        require(isinstance(raw_drivers, list), 'policy.drivers', 'Укажите драйверы.', code=code)
        drivers = {}
        for i, driver in enumerate(raw_drivers):
            field = f'policy.drivers[{i}]'
            require(isinstance(driver, dict), field, 'Требуется объект драйвера.', code=code)
            identity = text(driver.get('id'), field + '.id', identifier=True, code=code)
            require(identity not in drivers, field + '.id', 'Повторный драйвер.', code=code)
            prefix = driver.get('argv_prefix')
            require(isinstance(prefix, list) and bool(prefix)
                    and all(isinstance(v, str) and '\x00' not in v for v in prefix) and bool(prefix[0]),
                    field + '.argv_prefix', 'Укажите непустой список аргументов.', code=code)
            for key in ('cwd', 'path_prefix'):
                text(driver.get(key), field + '.' + key, code=code)
                if driver[key] != '.':
                    paths([driver[key]], field + '.' + key, code=code)
            drivers[identity] = driver
        profiles_raw, profiles_sha = read_raw(profiles_path, 'invalid_profiles', 'profiles', toml=True)
        profiles = ProfileCatalogue(profiles_raw)
        packages = None
        if policy['packages_path'] is not None:
            raw, _ = read_raw(Path(policy['packages_path']), 'invalid_packages', 'packages', toml=True)
            packages = GeneralPackageCatalogue(raw, drivers.keys())
        suites = None
        if policy['suites_path'] is not None:
            raw, _ = read_raw(Path(policy['suites_path']), 'invalid_suites', 'suites')
            suites = SuiteCatalogue(raw)
        return cls(bound, policy_path, policy, policy_sha, profiles_raw, profiles_sha,
                   profiles, packages, suites, drivers, state)
