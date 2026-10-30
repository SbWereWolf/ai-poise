"""V02: real SDK execution of the configured update gate; independent inputs."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/live_deployment'
GATE = ROOT / 'config/maintenance/update-check.example.json'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


@pytest.fixture(scope='module')
def sdk(tmp_path_factory):
    descriptor = Path(os.environ['AIP_LIVE_DEPLOYMENT_RUNTIME'])
    value = json.loads(descriptor.read_text())
    result = subprocess.run([value['sdk_entry'], '--version'], capture_output=True)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, b'environment-maintenance 2.0.0\n', b''), 'SDK prerequisite must pass before RED'
    root = tmp_path_factory.mktemp('independent-sdk-control')
    for name in ('infra', 'deps'):
        write(root / (name + '.json'), {'requirements': []})
    write(root / 'check.json', json.loads((FIXTURES / 'sdk-control-check.json').read_text()))
    write(root / 'app.json', {'operations': {'infra': 'infra.json', 'deps': 'deps.json',
        'check': 'check.json'}, 'interpreters': {'exit': {
            'module': 'poise_environment.result_interpreters', 'attribute': 'exit_result'}}})
    marker = root / 'control-marker'
    write(root / 'parameters.json', {'control_python': value['sdk_python'],
        'control_script': str(FIXTURES / 'sdk-control.py'),
        'control_root': str(root), 'control_marker': str(marker)})
    env = {'PATH': value['path'], 'LANG': 'C.UTF-8',
           'PYTHONPATH': os.pathsep.join([str(ROOT / 'src'), str(ROOT)])}
    result = subprocess.run([value['sdk_entry'], 'check', '--config', str(root / 'app.json'),
        '--parameters', str(root / 'parameters.json'), '--base-dir', str(root)],
        cwd=root, env=env, capture_output=True)
    (root / 'control.stdout').write_bytes(result.stdout)
    (root / 'control.stderr').write_bytes(result.stderr)
    assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'succeeded', (
        'SDK execution prerequisite, not product RED', result.stdout, result.stderr)
    assert marker.read_bytes() == b'healthy2.0-control\n'
    return value


@pytest.fixture
def consumer_case(tmp_path, sdk):
    inventory = json.loads((FIXTURES / 'inventory-valid.json').read_text())
    inventory_path = write(tmp_path / 'inventory.json', inventory)
    value = json.loads((FIXTURES / 'consumer-config.example.json').read_text())
    value['inventory'] = {'argv': [sys.executable, str(FIXTURES / 'inventory.py'),
        str(inventory_path)], 'cwd': str(tmp_path), 'env': {}}
    value['capture_directory'] = str(tmp_path / 'captures')
    for name in ['erp', 'poise-op', 'stride', 'workspace-recover', 'environment-maintenance-repair']:
        preparation = write(tmp_path / name / 'preparation.json',
            {'parameters': {'start_app': sdk['sdk_entry']}})
        runtime = None
        if name == 'erp':
            file = tmp_path / name / 'runtime.env'
            file.write_text('ERP_ENVIRONMENT_PYTHON_BIN=' + sdk['sdk_python'] + '\n')
            runtime = {'path': str(file), 'python_key': 'ERP_ENVIRONMENT_PYTHON_BIN'}
        diagnostic_root = tmp_path / name / 'compatibility'
        for operation in ('infra', 'deps'):
            write(diagnostic_root / (operation + '.json'), {'requirements': []})
        declaration = write(diagnostic_root / 'declaration.json', {'project': name,
            'marker': str(diagnostic_root / 'observed.txt'), 'exit_code': 0})
        action = {'id': 'consumer-route', 'commands': [{
            'argv': [sdk['sdk_python'], '-I', '-B',
                str(FIXTURES / 'compatibility-control.py'), str(declaration)],
            'cwd': str(diagnostic_root), 'env': {},
            'interpreter': {'name': 'exit', 'options': {'accept': [0]}}}]}
        write(diagnostic_root / 'check.json', {'requirements': [action]})
        app = write(diagnostic_root / 'app.json', {'operations': {
            'infra': 'infra.json', 'deps': 'deps.json', 'check': 'check.json'},
            'interpreters': {'exit': {'module': 'poise_environment.result_interpreters',
                                     'attribute': 'exit_result'}}})
        parameters = write(diagnostic_root / 'parameters.json', {})
        compatibility = {'argv': [sdk['sdk_entry'], 'check', '--config', str(app),
            '--parameters', str(parameters), '--base-dir', str(diagnostic_root)],
            'cwd': str(diagnostic_root), 'env': {'PATH': sdk['path'], 'LANG': 'C.UTF-8',
                'PYTHONPATH': os.pathsep.join([str(ROOT / 'src'), str(ROOT)])},
            'expected_exit_code': 0, 'expected_status': 'succeeded'}
        value['consumers'].append({'project': name, 'preparation_config': str(preparation),
                                  'runtime': runtime, 'compatibility': compatibility})
    return dict(root=tmp_path, sdk=sdk, inventory=inventory,
        inventory_path=inventory_path, config=value,
        config_path=tmp_path / 'consumer-configuration.json')


def invoke(case):
    # Absence of this shipped public configuration is the missing product gate,
    # not a missing test interpreter/package. The SDK prerequisite already ran.
    assert GATE.is_file(), 'No configured all-consumer update gate is shipped'
    root = case['root']
    write(case['inventory_path'], case['inventory'])
    write(case['config_path'], case['config'])
    write(root / 'check.json', json.loads(GATE.read_text()))
    for name in ('infra', 'deps'):
        write(root / (name + '.json'), {'requirements': []})
    write(root / 'app.json', {'operations': {'infra': 'infra.json', 'deps': 'deps.json',
        'check': 'check.json'}, 'interpreters': {'exit': {
            'module': 'poise_environment.result_interpreters', 'attribute': 'exit_result'}}})
    write(root / 'parameters.json', {'python': sys.executable,
        'runtime_module_path': os.pathsep.join([str(ROOT / 'src'), str(ROOT)]),
        'consumer_config': str(case['config_path'])})
    env = {'PATH': case['sdk']['path'], 'LANG': 'C.UTF-8',
           'PYTHONPATH': os.pathsep.join([str(ROOT / 'src'), str(ROOT)])}
    result = subprocess.run([case['sdk']['sdk_entry'], 'check', '--config', str(root / 'app.json'),
        '--parameters', str(root / 'parameters.json'), '--base-dir', str(root)],
        cwd=root, env=env, capture_output=True)
    packet = json.loads(result.stdout)
    (root / 'gate.stdout').write_bytes(result.stdout)
    (root / 'gate.stderr').write_bytes(result.stderr)
    assert len(packet['checks']) == 1, packet
    command = packet['checks'][0]['result']['commands'][0]
    diagnostic = json.loads(base64.b64decode(command['stdout']['data']))
    return result, packet, diagnostic


def test_all_five_consumers_are_observed_with_selected_entry_and_sdk(consumer_case):
    result, packet, diagnostic = invoke(consumer_case)
    assert result.returncode == 0 and packet['status'] == 'succeeded', (result.stderr, packet)
    assert diagnostic['status'] == 'ready' and diagnostic['errors'] == []
    expected = json.loads((FIXTURES / 'expected-consumers.json').read_text())
    keys = ['project', 'entry_version', 'sdk_version', 'compatibility_status', 'ready']
    actual = [{key: row[key] for key in keys} for row in diagnostic['consumers']]
    assert sorted(actual, key=lambda row: row['project']) == expected
    for consumer in consumer_case['config']['consumers']:
        marker = Path(consumer['compatibility']['cwd']) / 'observed.txt'
        assert marker.read_text() == consumer['project'] + '\n'


@pytest.mark.parametrize('defect', ['old-entry', 'wrong-sdk', 'missing-binding',
    'new-registration', 'invalid-inventory', 'unclassified-fixture', 'broken-compatibility'])
def test_an_incomplete_update_never_passes_the_configured_gate(consumer_case, defect):
    case = consumer_case
    if defect == 'old-entry':
        entry = case['root'] / 'old-tool'
        shutil.copyfile(FIXTURES / 'version-1.sh', entry)
        entry.chmod(0o700)
        write(Path(case['config']['consumers'][0]['preparation_config']),
              {'parameters': {'start_app': str(entry)}})
    elif defect == 'wrong-sdk':
        runtime = case['config']['consumers'][0]['runtime']
        Path(runtime['path']).write_text('ERP_ENVIRONMENT_PYTHON_BIN=/usr/bin/python3.12\n')
    elif defect == 'missing-binding':
        case['config']['consumers'] = case['config']['consumers'][:-1]
    elif defect == 'new-registration':
        case['inventory']['projects'].append({'project': 'new-application', 'config_path': 'new.json'})
    elif defect == 'invalid-inventory':
        case['inventory']['status'] = 'listed_with_errors'
        case['inventory']['errors'] = [{'project': 'workspace-recover', 'reason': 'invalid workflow'}]
    elif defect == 'unclassified-fixture':
        case['config']['excluded_projects'] = []
    else:
        diagnostic = Path(case['config']['consumers'][0]['compatibility']['cwd'])
        declaration = json.loads((diagnostic / 'declaration.json').read_text())
        declaration['exit_code'] = 7
        write(diagnostic / 'declaration.json', declaration)
    result, packet, diagnostic = invoke(case)
    assert result.returncode != 0 and packet['status'] == 'failed', packet
    assert diagnostic['status'] == 'error' and diagnostic['errors'], diagnostic


@pytest.mark.parametrize('compatible', [True, False])
def test_real_sdk_compatibility_controls_have_the_observed_result_contract(consumer_case, compatible):
    case = consumer_case
    for consumer in case['config']['consumers']:
        invocation = consumer['compatibility']
        root = Path(invocation['cwd'])
        declaration = json.loads((root / 'declaration.json').read_text())
        declaration['exit_code'] = 0 if compatible else 7
        write(root / 'declaration.json', declaration)
        result = subprocess.run(invocation['argv'], cwd=root,
            env=invocation['env'], capture_output=True)
        (root / 'control.stdout').write_bytes(result.stdout)
        (root / 'control.stderr').write_bytes(result.stderr)
        packet = json.loads(result.stdout)
        assert result.returncode == (0 if compatible else 1)
        assert packet['status'] == ('succeeded' if compatible else 'failed')
        outcome = packet['checks'][0]['result']
        assert outcome['status'] == ('succeeded' if compatible else 'failed')
        assert outcome['commands'][0]['interpretation']['success'] is compatible
        assert (root / 'observed.txt').read_text() == consumer['project'] + '\n'


def test_gate_preserves_all_consumer_and_fixture_inputs(consumer_case):
    case = consumer_case
    write(case['inventory_path'], case['inventory'])
    write(case['config_path'], case['config'])
    inputs = [case['inventory_path'], case['config_path']]
    inputs.extend(Path(row['preparation_config']) for row in case['config']['consumers'])
    inputs.extend(Path(row['runtime']['path']) for row in case['config']['consumers'] if row['runtime'])
    for row in case['config']['consumers']:
        inputs.extend(Path(row['compatibility']['cwd']).glob('*.json'))
    before = {path: path.read_bytes() for path in inputs}
    result, packet, diagnostic = invoke(case)
    assert result.returncode == 0 and diagnostic['status'] == 'ready', packet
    assert {path: path.read_bytes() for path in inputs} == before
