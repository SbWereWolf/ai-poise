"""Real CLI Unicode inputs with independent envelopes and state-effect checks."""
import json
import subprocess
import sys

import pytest

from tests.placement.helpers import Scenario, diagnostic, item, literal


def invoke(scenario, packet):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'poise', 'placement',
         '--config', str(scenario.config), '--checkout', str(scenario.checkout),
         '--policy', str(scenario.policy)],
        input=json.dumps(packet, ensure_ascii=True).encode('ascii'),
        cwd=scenario.checkout, env=scenario.env, capture_output=True,
    )


def rejected(result, code, field, action):
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == b''
    expected = literal('rejected.json')
    expected['error'].update(code=code, field=field, action=action)
    assert json.loads(result.stdout.decode('utf-8')) == expected


@pytest.mark.parametrize('operation', ['prepare', 'check'])
@pytest.mark.parametrize('field', ['name', 'path'])
@pytest.mark.parametrize('character', ['\ud800', '\udc80'], ids=['high', 'low'])
def test_unencodable_request_path_is_rejected(project, tmp_path, operation, field, character):
    scenario = Scenario(project, tmp_path)
    before = scenario.initial()
    entry = item('unicode-probe', name='new-report.py')
    if field == 'name':
        entry['name'] = character + '.py'
    else:
        entry['path'] = 'src/' + character + '/new-report.py'
    result = invoke(scenario, scenario.request(operation=operation, items=[entry]))
    assert scenario.state.read_bytes() == before
    rejected(result, 'invalid_paths', 'items', 'provide:paths')


@pytest.mark.parametrize('operation', ['prepare', 'check'])
def test_valid_unicode_name_preserves_its_meaning(project, tmp_path, operation):
    scenario = Scenario(project, tmp_path)
    before = scenario.initial()
    name = 'report-Ж-𐀀.py'
    result = invoke(scenario, scenario.request(
        operation=operation, items=[item('unicode-probe', name=name)]))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == b''
    response = json.loads(result.stdout.decode('utf-8'))
    assert response['status'] == 'ready'
    assert response['items'][0]['path'] == 'src/' + name
    if operation == 'check':
        assert scenario.state.read_bytes() == before
    else:
        records = {record['key']: record for record in scenario.read()['reservations']}
        assert records['unicode-probe']['name'] == name
        assert records['unicode-probe']['path'] == 'src/' + name


@pytest.mark.parametrize('field', [
    'root.path',
    'rule.directory',
    'rule.paths',
    'state.directory',
    'state.filename',
    'state.lock',
])
def test_unencodable_required_policy_path_is_rejected(project, tmp_path, field):
    scenario = Scenario(project, tmp_path)
    before = scenario.initial()
    policy = scenario.policy_data
    if field == 'root.path':
        policy['roots'][0]['path'] += '/\ud800'
    elif field == 'rule.directory':
        policy['rules'][0]['directory'] = 'src/\ud800'
    elif field == 'rule.paths':
        policy['rules'][0]['paths'] = ['src/\ud800/*.py']
    else:
        key = field.split('.')[1]
        policy['state'][key] += '\ud800'
    scenario.policy.write_bytes(json.dumps(policy, ensure_ascii=True).encode('ascii'))
    result = invoke(scenario, scenario.request())
    assert scenario.state.read_bytes() == before
    rejected(result, 'invalid_policy', 'policy', 'correct:policy')


@pytest.mark.parametrize('operation', ['prepare', 'check'])
@pytest.mark.parametrize('field', ['baseline', 'path', 'absolute_path'])
def test_unencodable_selected_state_path_is_diagnosed(project, tmp_path, operation, field):
    scenario = Scenario(project, tmp_path)
    scenario.initial()
    state = scenario.read()
    if field == 'baseline':
        state['baseline']['entries'] = sorted(state['baseline']['entries'] + ['\ud800'])
    elif field == 'path':
        state['reservations'][0]['path'] = 'src/\ud800/report.py'
    else:
        state['reservations'][0]['absolute_path'] = str(scenario.checkout) + '/src/\ud800/report.py'
    before = json.dumps(state, ensure_ascii=True).encode('ascii')
    scenario.state.write_bytes(before)
    packet = scenario.request(
        operation=operation, items=[item('new-identity', name='new-destination.py')])
    result = invoke(scenario, packet)
    assert scenario.state.read_bytes() == before
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == b''
    response = json.loads(result.stdout.decode('utf-8'))
    scenario.assert_result(
        (result.returncode, response), status='needs_inputs',
        rows=[scenario.row(key='new-identity', reserved=False, path='src/new-destination.py',
                           absolute_path=str(scenario.checkout) + '/src/new-destination.py')],
        diagnostics=[diagnostic('invalid_state', None, 'state', 'inspect:state')],
    )
