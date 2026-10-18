"""DoD3: fresh ERP sources reach real readiness through Task creation itself."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest
pytest.register_assert_rewrite('erp_support')
from conftest import git, write_json
from erp_support import (
    CONTRACT, FIXTURES, bootstrap_configuration, export_erp_source, invoke,
    materialize, require_input, targets,
)
from test_catalog_v2_profile import consumer, source  # Existing fixture owners.
from test_erp_environment_acceptance import assert_real_dependencies
from runtime_services.worktree_preparation_support import (
    arrange, assert_registered_materials, boot, material_bytes, read_json, receipt,
)


def selected_shared_source(repository, destination):
    replacements = {
        **CONTRACT['application_databases'], **CONTRACT['queue_databases'],
        'COMPOSER_BIN': require_input('P003_COMPOSER_EXECUTABLE'),
        'PHP_BIN': require_input('P003_PHP_EXECUTABLE'),
        'NODE_BIN': require_input('P003_NODE_EXECUTABLE'),
        'ERP_DRAFT_SLAB_COUNT_LIMIT': '13',
    }
    template = repository / 'platform-infrastructure/config/dev/local-development.example.env'
    lines, found = [], set()
    for line in template.read_text().splitlines():
        key = line.partition('=')[0]
        if key in replacements:
            line = key + '=' + json.dumps(replacements[key], ensure_ascii=False)
            found.add(key)
        lines.append(line)
    required = set(CONTRACT['application_databases']) | set(CONTRACT['queue_databases']) | {'ERP_DRAFT_SLAB_COUNT_LIMIT'}
    assert required <= found, 'Selected ERP example must define the declared application/queue inputs'
    for key in sorted(set(replacements) - found):
        lines.append(key + '=' + json.dumps(replacements[key], ensure_ascii=False))
    destination.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def assert_ready_engine_and_inputs(case, response, desired):
    report = receipt(response, case['preparation'])
    assert report['status'] == 'ready'
    assert [operation['name'] for operation in report['operations']] == ['infra', 'deps', 'check']
    assert_registered_materials(case['preparation'], report)
    final = report['operations'][-1]['commands'][-1]
    engine = json.loads(Path(final['stdout_path']).read_bytes())
    assert final['returncode'] == 0
    assert engine['schema'] == 'environment-maintenance/result/v2' and engine['status'] == 'ready'
    expected = [identifier for group in CONTRACT['groups'].values() for identifier in group]
    actual = engine['requirements']
    assert len(actual) == len(expected) == 56
    assert {row['id'] for row in actual} == set(expected)
    assert all(row['state'] == 'satisfied' for row in actual)
    for target in targets(case):
        assert target['path'].read_bytes() == desired[target['id']]
    for relative in ('planning-workspace/.env', 'calculation-service/.env',
                     'system-admin/.env', 'document-ui/.env'):
        assert (case['repository'] / relative).is_file()
    assert_real_dependencies(case)
    node = require_input('P003_NODE_EXECUTABLE')
    usable = invoke([node, FIXTURES / 'erp_vite_entry.mjs',
                     case['repository'] / 'document-ui/node_modules/vite/dist/node/index.js'])
    assert usable.returncode == 0 and usable.stdout == 'function', (usable.stdout, usable.stderr)
    return report


def assert_database_tables_and_rows(case, *, insert):
    repository = case['repository']
    for application, expected in CONTRACT['required_tables'].items():
        key = 'PLANNING_WORKSPACE_DATABASE_PATH' if application == 'planning-workspace' else 'SYSTEM_ADMIN_DATABASE_PATH'
        with sqlite3.connect(repository / CONTRACT['application_databases'][key]) as db:
            tables = {row[0] for row in db.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',))}
            assert set(expected) <= tables
            if insert:
                db.execute('CREATE TABLE p004_retained (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('INSERT INTO p004_retained (id,value) VALUES (?,?)', (17, 'retained Ω'))
            assert db.execute('SELECT id,value FROM p004_retained WHERE id=?', (17,)).fetchall() == [(17, 'retained Ω')]
    for relative in CONTRACT['queue_databases'].values():
        with sqlite3.connect(repository / relative) as db:
            tables = {row[0] for row in db.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',))}
            assert {'jobs', 'failed_jobs', 'erp_queue_schema'} <= tables
            if insert:
                db.execute('INSERT INTO jobs (id,queue,payload,attempts,reserved_at,available_at,created_at) '
                           'VALUES (?,?,?,?,?,?,?)', (71, 'p004', '{}', 0, None, 1, 1))
            assert db.execute('SELECT id,queue,payload,attempts FROM jobs WHERE id=?', (71,)).fetchall() == [(71, 'p004', '{}', 0)]


def test_fresh_managed_erp_workspace_reaches_real_ready_and_keeps_history(project, tmp_path, monkeypatch, source, consumer):
    monkeypatch.setenv('P003_CONSUMER_PYTHON', str(consumer))
    invocations = []
    popen = subprocess.Popen.__init__

    def observe_process(process, args, *more, **kwargs):
        if isinstance(args, (list, tuple)) and 'environment_maintenance' in list(map(str, args)):
            invocations.append(list(map(str, args)))
        return popen(process, args, *more, **kwargs)

    monkeypatch.setattr(subprocess.Popen, '__init__', observe_process)
    project['cfg']['batch']['max_artifact_bytes'] = 4 * 1024 * 1024
    preparation = arrange(project, monkeypatch)
    origin = {'repository': project['app']}
    export_erp_source(origin)
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Selected tracked ERP source closure')
    assert preparation['runtime'].cfg['paths']['worktrees'] == 'worktrees'
    future = preparation['runtime'].state / 'worktrees/T1'
    assert not future.exists()
    case = {'root': tmp_path, 'supply': source, 'repository': future,
            'profile': project['root'] / 'operator/erp-catalog',
            'state': tmp_path / 'separate-maintenance-state', 'preparation': preparation}
    result = materialize(case, project='erp-managed-inputs')
    assert result.returncode == 0, (result.stdout, result.stderr)
    operator = project['root'] / 'operator'
    shared = operator / 'selected-shared.env'
    selected_shared_source(project['app'], shared)
    execution = project['app'] / 'platform-infrastructure/tests/playwright/contracts/fixtures/cjm-configuration/test-execution.json'
    configured = bootstrap_configuration(case)
    configured['catalog_dir'] = str(case['profile'])
    configured['operations'] = ['infra', 'deps', 'check']
    configured['inputs'] = [
        {'source': str(shared), 'destination': 'platform-infrastructure/config/dev/local-development.env'},
        {'source': str(execution), 'destination': CONTRACT['normal_cjm']['execution_configuration']},
    ]
    preparation['fixture_streams'] = False
    write_json(preparation['configuration'], configured)
    desired = {target['id']: (case['profile'] / 'documents' / (target['id'] + '.json')).read_bytes()
               for target in targets(case)}
    for name in ('COMPOSER_HOME', 'COMPOSER_CACHE_DIR', 'npm_config_cache'):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(name, str(directory))
    # No standalone environment action, .env generation or input write in future worktree.
    first = boot(preparation)
    assert first['worktree'] == str(future)
    assert first['preparation']['status'] == 'ready'
    before_report = assert_ready_engine_and_inputs(case, first, desired)
    assert_database_tables_and_rows(case, insert=True)
    retained = future / 'retained-user-file.txt'
    retained.write_text('user work Ω', encoding='utf-8')
    before = material_bytes(preparation)
    cjm_source = case['profile'] / 'documents/cjm-settings.json'
    edited = read_json(cjm_source)
    edited['businessFixture']['wirePrice'] = 16
    write_json(cjm_source, edited)
    desired['cjm-settings'] = cjm_source.read_bytes()
    second = boot(preparation, again=True, request_id='erp-settings-2')
    assert second['worktree'] == first['worktree']
    after_report = assert_ready_engine_and_inputs(case, second, desired)
    assert after_report['attempt_id'] != before_report['attempt_id']
    assert retained.read_text() == 'user work Ω'
    assert_database_tables_and_rows(case, insert=False)
    assert all(Path(path).read_bytes() == content for path, content in before.items())
    selected = next(target for target in targets(case) if target['id'] == 'cjm-settings')
    loader = future / 'platform-infrastructure/tests/playwright/support/cjm-configuration.mjs'
    loaded = invoke([require_input('P003_NODE_EXECUTABLE'), FIXTURES / 'erp_cjm_consumer.mjs',
                     loader, future, selected['relative']])
    assert loaded.returncode == 0, (loaded.stdout, loaded.stderr)
    assert json.loads(loaded.stdout) == {'wirePrice': 16, 'setupPrice': 0, 'draftSlabCountLimit': 13}
    inventory = material_bytes(preparation)
    count = len(invocations)
    old_request = boot(preparation, again=True, request_id='erp-settings-2')
    assert old_request['preparation'] == second['preparation']
    assert len(invocations) == count
    assert material_bytes(preparation) == inventory
    assert hashlib.sha256(selected['path'].read_bytes()).hexdigest() == hashlib.sha256(desired['cjm-settings']).hexdigest()
