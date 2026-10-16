"""R3: actual selected ERP locks/owners in isolated source and storage.

Missing managers/network/source inputs fail setup; they are never RED or skips.
No business/browser scenario or service is started.
"""
import json
from pathlib import Path
import sqlite3

import pytest
pytest.register_assert_rewrite('erp_support')
from erp_support import (
    CONTRACT, FIXTURES, action, bootstrap, export_erp_source, invoke, observations,
    observe_exact, publish_configuration_inputs, read_json, require_input, targets,
)

pytest_plugins = ['erp_support']


def assert_action(case, identifier, role, state):
    result, response = action(case, identifier, role)
    assert result.returncode == 0, (identifier, result.stdout, result.stderr)
    assert response['state' if role == 'check' else 'outcome'] == state, response


def arrange_shared_environment(case):
    """Known sanitized example input; edits are explicit test data, not a parser."""
    repository = case['repository']
    example = repository / 'platform-infrastructure/config/dev/local-development.example.env'
    replacements = {
        **CONTRACT['application_databases'], **CONTRACT['queue_databases'],
        'COMPOSER_BIN': require_input('P003_COMPOSER_EXECUTABLE'),
        'PHP_BIN': require_input('P003_PHP_EXECUTABLE'),
        'NODE_BIN': require_input('P003_NODE_EXECUTABLE'),
    }
    lines = example.read_text().splitlines()
    found = set()
    for index, line in enumerate(lines):
        key = line.partition('=')[0]
        if key in replacements:
            lines[index] = key + '=' + json.dumps(replacements[key], ensure_ascii=False)
            found.add(key)
    assert set(CONTRACT['application_databases']) | set(CONTRACT['queue_databases']) <= found
    destination = example.with_name('local-development.env')
    destination.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def assert_real_dependencies(case):
    repository = case['repository']
    php = require_input('P003_PHP_EXECUTABLE')
    for application in CONTRACT['php_applications']:
        root = repository / application
        assert (root / 'composer.lock').is_file()
        result = invoke([php, FIXTURES / 'erp_assert_autoload.php',
                         root / 'vendor/autoload.php'], cwd=root)
        assert result.returncode == 0 and result.stdout == 'usable', (result.stdout, result.stderr)
        assert (root / 'bootstrap/cache/packages.php').is_file(), 'Real package discovery effect'
        assert_action(case, 'erp-composer-' + application, 'check', 'satisfied')
        assert_action(case, 'erp-discovery-' + application, 'check', 'satisfied')
    for application in CONTRACT['node_applications']:
        root = repository / application
        assert (root / 'package-lock.json').is_file()
        assert (root / 'node_modules/.package-lock.json').is_file()
    for identifier in ('erp-node-document-ui', 'erp-node-playwright', 'erp-playwright',
                       'erp-cli', 'erp-verifier', 'erp-xsd', 'erp-mirrored-packages'):
        assert_action(case, identifier, 'check', 'satisfied')


def assert_databases_and_preserve_rows(case):
    """Independent SQL, not application repositories or generated expectations."""
    repository = case['repository']
    for application, expected in CONTRACT['required_tables'].items():
        key = 'PLANNING_WORKSPACE_DATABASE_PATH' if application == 'planning-workspace' else 'SYSTEM_ADMIN_DATABASE_PATH'
        path = repository / CONTRACT['application_databases'][key]
        assert path.is_file()
        with sqlite3.connect(path) as database:
            actual = {row[0] for row in database.execute(
                'SELECT name FROM sqlite_master WHERE type = ?', ('table',))}
            assert set(expected) <= actual
            database.execute('CREATE TABLE acceptance_retained (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
            database.execute('INSERT INTO acceptance_retained (id, value) VALUES (?, ?)', (17, 'retained Ω'))
    for relative in CONTRACT['queue_databases'].values():
        path = repository / relative
        assert path.is_file()
        with sqlite3.connect(path) as database:
            actual = {row[0] for row in database.execute(
                'SELECT name FROM sqlite_master WHERE type = ?', ('table',))}
            assert {'jobs', 'failed_jobs', 'erp_queue_schema'} <= actual
            database.execute(
                'INSERT INTO jobs (id, queue, payload, attempts, reserved_at, available_at, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)', (71, 'acceptance', '{}', 0, None, 1, 1))
    for identifier in ('erp-application-databases', 'erp-queue-schema',
                       'erp-migrations-planning-workspace', 'erp-migrations-system-admin',
                       'erp-bootstrap-settings'):
        assert_action(case, identifier, 'repair', 'completed')
        assert_action(case, identifier, 'check', 'satisfied')
    for relative in CONTRACT['application_databases'].values():
        with sqlite3.connect(repository / relative) as database:
            assert database.execute('SELECT id, value FROM acceptance_retained WHERE id = ?',
                                    (17,)).fetchall() == [(17, 'retained Ω')]
    for relative in CONTRACT['queue_databases'].values():
        with sqlite3.connect(repository / relative) as database:
            assert database.execute('SELECT id, queue, payload, attempts FROM jobs WHERE id = ?',
                                    (71,)).fetchall() == [(71, 'acceptance', '{}', 0)]


def assert_unit_maintenance(case):
    environment = case['repository'] / 'planning-workspace/.env'
    original = environment.read_text()
    declared = CONTRACT['unit_values']
    # The real PHP dotenv dependency is installed by the preceding owner.
    assert_action(case, 'erp-installation-units', 'check', 'satisfied')
    retained = 'P003_RETAINED_VALUE="operator Ω"\n'
    missing = '\n'.join(line for line in original.splitlines()
                        if not line.startswith('QUANTITY_UNITS_LENGTH=')) + '\n' + retained
    environment.write_text(missing, encoding='utf-8')
    assert_action(case, 'erp-installation-units', 'check', 'invalid')
    assert_action(case, 'erp-installation-units', 'repair', 'completed')
    assert retained in environment.read_text()
    php = require_input('P003_PHP_EXECUTABLE')
    parsed = invoke([php, FIXTURES / 'erp_read_dotenv.php',
                     environment.parent / 'vendor/autoload.php', environment])
    assert parsed.returncode == 0, parsed.stderr
    actual = json.loads(parsed.stdout)
    assert {key: actual[key] for key in declared} == declared
    assert_action(case, 'erp-installation-units', 'check', 'satisfied')
    good = environment.read_text()
    for invalid in ('QUANTITY_UNITS_LENGTH=\n', 'QUANTITY_UNITS_LENGTH=мм\n',
                    'QUANTITY_UNITS_LENGTH=${P003_UNDECLARED_UNIT}\n'):
        if invalid == 'QUANTITY_UNITS_LENGTH=мм\n':
            changed = good + invalid  # A duplicate declaration, even when value agrees.
        else:
            changed = '\n'.join(line for line in good.splitlines()
                                if not line.startswith('QUANTITY_UNITS_LENGTH=')) + '\n' + invalid
        environment.write_text(changed, encoding='utf-8')
        try:
            assert_action(case, 'erp-installation-units', 'check', 'invalid')
        finally:
            environment.write_text(good, encoding='utf-8')


def assert_local_package_refresh(case):
    package = case['repository'] / 'planning-workspace-core'
    name = read_json(package / 'composer.json')['name']
    marker = package / 'p003-refresh-marker.txt'
    expected = 'Changed local package input Ω\n'
    marker.write_text(expected, encoding='utf-8')
    assert_action(case, 'erp-mirrored-packages', 'repair', 'completed')
    autoload = case['repository'] / 'planning-workspace/vendor/autoload.php'
    located = invoke([require_input('P003_PHP_EXECUTABLE'),
                      FIXTURES / 'erp_package_path.php', autoload, name])
    assert located.returncode == 0 and located.stdout, (located.stdout, located.stderr)
    assert (Path(located.stdout) / marker.name).read_text() == expected
    assert_action(case, 'erp-mirrored-packages', 'check', 'satisfied')


def test_actual_selected_erp_reaches_readiness_and_preserves_repeated_state(erp_deployed, monkeypatch):
    case = export_erp_source(erp_deployed)
    # Real installation caches are test-owned; HOME itself remains unchanged.
    for variable, relative in [('COMPOSER_HOME', 'composer-home'),
                               ('COMPOSER_CACHE_DIR', 'composer-cache'),
                               ('npm_config_cache', 'npm-cache')]:
        directory = case['root'] / relative
        directory.mkdir()
        monkeypatch.setenv(variable, str(directory))
    publish_configuration_inputs(case)
    desired = {t['id']: (case['profile'] / 'documents' / (t['id'] + '.json')).read_bytes()
               for t in targets(case)}
    arrange_shared_environment(case)
    assert_action(case, 'erp-environment', 'install', 'completed')
    assert_action(case, 'erp-environment', 'check', 'satisfied')
    for operation in ('infra', 'deps', 'check'):
        result, report = bootstrap(case, operation)
        assert result.returncode == 0 and report['status'] == 'ready', (operation, report)
        for target in targets(case):
            assert target['path'].read_bytes() == desired[target['id']]
            observe_exact(observations(report)[target['id']], identifier=target['id'],
                          kind=target['kind'], content=desired[target['id']])
    assert_real_dependencies(case)
    assert_databases_and_preserve_rows(case)
    assert_unit_maintenance(case)
    assert_local_package_refresh(case)
    before = {t['id']: t['path'].read_bytes() for t in targets(case)}
    result, report = bootstrap(case, 'infra')
    assert result.returncode == 0 and report['status'] == 'ready', report
    assert {t['id']: t['path'].read_bytes() for t in targets(case)} == before
    assert_action(case, 'erp-readiness', 'check', 'satisfied')
