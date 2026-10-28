"""Healthy arrangements for operational assertions before actual deployments."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import shlex

import pytest

from live_application_support import consumer_operation, requirements_paths, verify_launch, write
from live_launch_window import run_declared_entry
from test_consumer_update_gate import consumer_case, sdk


@pytest.mark.parametrize('absolute', [False, True])
def test_requirements_store_arrangement_resolves_database_and_lock(tmp_path, absolute):
    from poise.application.requirements_registry import RequirementsCommands
    from poise.infrastructure.requirements_registry import RequirementsStore

    state = tmp_path / 'application-state'
    state.mkdir()
    relative = ('database/requirements.sqlite', 'database/requirements.lock')
    paths = [str(state / value) if absolute else value for value in relative]
    manifest = {'paths': dict(state=str(state), requirements_database=paths[0],
                              requirements_lock=paths[1])}
    database, lock = requirements_paths(manifest)
    assert database == state / relative[0] and lock == state / relative[1]
    database.parent.mkdir()
    store = RequirementsStore(database, lock, 2.0, 0.01)
    RequirementsCommands(store, 10).apply({'request_id': 'seed-path-control',
        'expected_revision': 0, 'operations': [{'kind': 'put_requirement',
            'requirement': {'id': 'PATH-CONTROL', 'level': 'system',
                'status': 'current', 'text': 'Existing rows survive preparation.'}}]})
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
        assert connection.execute('SELECT id, text FROM requirements WHERE id = ?',
            ('PATH-CONTROL',)).fetchone() == ('PATH-CONTROL', 'Existing rows survive preparation.')


@pytest.mark.parametrize('failed_operand', ['none', 'presence', 'availability', 'additional'])
def test_real_sdk_resource_and_additional_checks_use_status_results(consumer_case, failed_operand):
    selected = consumer_case['config']['consumers'][0]['compatibility']
    root = Path(selected['cwd'])
    original = json.loads((root / 'check.json').read_text())['requirements'][0]

    def action(name):
        value = deepcopy(original)
        value['id'] = name
        declaration = write(root / (name + '.json'), {'project': name,
            'marker': str(root / (name + '.observed')),
            'exit_code': 7 if failed_operand == name else 0})
        value['commands'][0]['argv'][-1] = str(declaration)
        return value

    presence, availability = action('presence'), action('availability')
    write(root / 'infra.json', {'requirements': [{'id': 'resource',
        'commands': presence['commands'],
        'checks': {'presence': presence, 'availability': availability}}]})
    write(root / 'check.json', {'requirements': [action('additional')]})
    result = subprocess.run(selected['argv'], cwd=root, env=selected['env'],
                            capture_output=True, timeout=30)
    (root / 'resource-control.stdout').write_bytes(result.stdout)
    (root / 'resource-control.stderr').write_bytes(result.stderr)
    packet = json.loads(result.stdout)
    success = failed_operand == 'none'
    assert result.returncode == (0 if success else 1)
    assert packet['status'] == ('succeeded' if success else 'failed')
    resource = packet['resources'][0]
    assert resource['presence']['status'] == ('failed' if failed_operand == 'presence' else 'succeeded')
    assert resource['availability']['status'] == (
        'not_attempted' if failed_operand == 'presence' else
        'failed' if failed_operand == 'availability' else 'succeeded')
    additional = packet['checks'][0]['result']
    assert additional['status'] == ('failed' if failed_operand == 'additional' else 'succeeded')
    assert additional['commands'][0]['interpretation']['success'] is (failed_operand != 'additional')


@pytest.mark.parametrize('valid_run', [True, False])
def test_launch_window_preserves_and_executes_the_original_run(consumer_case, valid_run):
    invocation = consumer_case['config']['consumers'][0]['compatibility']
    root = Path(invocation['cwd'])
    app, parameters = root / 'app.json', root / 'parameters.json'
    document = json.loads(app.read_text())
    document['operations']['run'] = 'check.json' if valid_run else 'missing-declared-run.json'
    write(app, document)
    before = {path: path.read_bytes() for path in (app, parameters, root / 'check.json')}
    argv = list(invocation['argv'])
    argv[1] = 'run'
    argv += ['--requirements', 'all']
    inputs = {'argv': argv, 'parameters': str(parameters), 'base': str(root),
        'evidence': str(root), 'environment': invocation['env'],
        'private_display': False, 'command_timeout': 30}
    result = run_declared_entry(inputs, {})
    assert result['actual_exit_code'] == (0 if valid_run else 2)
    assert result['capture_complete'] and not result['timed_out']
    assert {path: path.read_bytes() for path in before} == before
    assert (root / 'observed.txt').exists() is valid_run


def test_bootstrap_consumer_control_preserves_real_sdk_packet(consumer_case):
    from test_consumer_update_gate import ROOT

    selected = consumer_case['config']['consumers'][0]['compatibility']
    root = Path(selected['cwd'])
    app, parameters = root / 'app.json', root / 'parameters.json'
    document = json.loads(app.read_text())
    document['operations']['run'] = 'check.json'
    write(app, document)
    arguments = list(selected['argv'])
    arguments[1] = 'run'
    arguments += ['--requirements', 'all']
    write(root / 'bootstrap.json', {'parameters': {}, 'commands': {'run': arguments},
        'handler': {'callable': 'apps.bootstrap.environment_result:handle', 'options': {
            'schema': None, 'status_exit_codes': {'succeeded': 0, 'failed': 1},
            'success_statuses': ['succeeded'], 'repairable_statuses': [],
            'repair_operations': [], 'repair_command': 'repair',
            'verification_command': 'verify', 'failure_exit_code': 4}}})
    runtime = consumer_case['sdk'] | {'module_path': str(ROOT / 'src') + ':' + str(ROOT)}
    packet = consumer_operation(runtime, 'poise-op', 'run', app, parameters, ROOT,
                                 root, 'real-bootstrap-control')
    assert packet['requirements'][0]['result']['status'] == 'succeeded'
    assert (root / 'observed.txt').read_text() == 'erp\n'


def test_missing_declared_run_fails_before_any_console_window(consumer_case):
    from test_consumer_update_gate import ROOT
    selected = consumer_case['config']['consumers'][0]['compatibility']
    root = Path(selected['cwd'])
    app = root / 'app.json'
    document = json.loads(app.read_text())
    document['operations']['run'] = 'missing-declared-run.json'
    write(app, document)
    runtime = consumer_case['sdk'] | {'module_path': str(ROOT / 'src') + ':' + str(ROOT)}
    before = app.read_bytes()
    with pytest.raises(FileNotFoundError):
        verify_launch(runtime, 'erp', app, root / 'parameters.json', root, root, {})
    assert app.read_bytes() == before


def test_original_run_executes_in_a_private_display_without_rewriting_it(consumer_case):
    from test_consumer_update_gate import ROOT
    selected = consumer_case['config']['consumers'][0]['compatibility']
    root = Path(selected['cwd'])
    app, parameters = root / 'app.json', root / 'parameters.json'
    document = json.loads(app.read_text())
    document['operations']['run'] = 'check.json'
    write(app, document)
    write(parameters, {'display': ':0', 'xauthority': '/unused-host-authority',
                       'preserved_domain_value': 'same-instance'})
    action = json.loads((root / 'check.json').read_text())
    action['requirements'][0]['commands'][0]['env'] = {
        'DISPLAY': '${display}', 'XAUTHORITY': '${xauthority}'}
    write(root / 'check.json', action)
    # Transparent external consumer fixture, not a substitute for the actual
    # ERP source/working entry required by the GREEN operational methods.
    (root / 'start-app').write_text('#!/bin/bash\nexec ' +
        shlex.quote(consumer_case['sdk']['sdk_entry']) + ' "$@" --requirements all\n')
    evidence = root / 'window-evidence'
    evidence.mkdir()
    runtime = consumer_case['sdk'] | {'module_path': str(ROOT / 'src') + ':' + str(ROOT)}
    probe = ['/usr/bin/timeout', '15', '/bin/bash', '-c',
        'until [ -f "$1" ]; do sleep .05; done', 'fixture-readiness', str(root / 'observed.txt')]
    declaration = {'window_seconds': .2, 'command_timeout': 15,
        'bwrap': '/usr/bin/bwrap', 'xvfb_run': '/usr/bin/xvfb-run',
        'readiness': {'argv': probe, 'env': {'PATH': '/usr/bin:/bin'}, 'timeout_seconds': 20},
        'endpoints': [], 'process_ids': ['consumer-route']}
    verify_launch(runtime, 'erp', app, parameters, root, evidence, declaration)
    private_values = json.loads((evidence / 'private-display-parameters.json').read_text())
    assert private_values['display'] != ':0'
    assert private_values['xauthority'] != '/unused-host-authority'
    assert private_values['preserved_domain_value'] == 'same-instance'
