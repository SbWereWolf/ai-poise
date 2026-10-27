"""Real Poise CLI, independent subprocess replies, and exact recovery effects."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/self_service'


def encoded_reply(status='succeeded', code=0, *, raw=None, error=b''):
    body = json.dumps({'status': status}).encode() if raw is None else raw
    return {'stdout': base64.b64encode(body).decode(),
            'stderr': base64.b64encode(error).decode(), 'exit_code': code}


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / 'Poise выбранный источник'
    root.mkdir()
    shutil.copy2(ROOT / 'bootstrap', root / 'bootstrap')
    shutil.copytree(ROOT / 'apps/bootstrap', root / 'apps/bootstrap')
    shutil.copy2(FIXTURES / 'handlers.py', root / 'fixture_handlers.py')
    context = tmp_path / 'другой экземпляр с пробелами'
    context.mkdir()
    command = json.loads((FIXTURES / 'command.example.json').read_text())
    path = root / 'config.json'
    spec = root / 'producer.json'
    command['parameters'].update(python=sys.executable,
        script=str(FIXTURES / 'command.py'), spec=str(spec))
    value = {'log': str(root / 'calls.jsonl'), 'responses': {
        name: encoded_reply() for name in ('infra', 'deps', 'check', 'run', 'repair', 'verify')}}
    return dict(root=root, context=context, command=command, path=path, spec=spec, value=value)


def invoke(case, operation='infra'):
    case['path'].write_text(json.dumps(case['command']))
    case['spec'].write_text(json.dumps(case['value']))
    return subprocess.run([sys.executable, '-I', '-B', str(case['root'] / 'bootstrap'),
        operation, '--config', str(case['path']), '--base-dir', str(case['context'])],
        cwd=case['context'], capture_output=True)


def call_log(case):
    path = Path(case['value']['log'])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def report(reply, code=0):
    assert reply.returncode == code, (reply.stdout, reply.stderr)
    assert reply.stderr == b''
    return json.loads(reply.stdout)


@pytest.mark.parametrize('operation', ['infra', 'deps', 'check', 'run'])
def test_selected_operation_uses_standard_parameters_and_only_its_command(installation, operation):
    reply = invoke(installation, operation)
    # A deterministic minimal counterexample through ordinary pytest. Unknown
    # setup/import failures do not emit this missing-feature marker.
    if operation == 'run' and b"invalid choice: 'run'" in reply.stderr:
        print('CONFIGURED_RUN_NOT_SUPPORTED')
        pytest.fail('CONFIGURED_RUN_NOT_SUPPORTED', pytrace=False)
    value = report(reply)
    assert value['status'] == 'ready'
    assert call_log(installation) == [{'role': operation,
        'context': str(installation['context']), 'cwd': str(installation['context'])}]


@pytest.mark.parametrize('selector,code', [('first', 0), ('second', 7)])
def test_configured_handler_is_interchangeable_and_receives_actual_result(installation, selector, code):
    installation['command']['handler'] = {'callable': f'fixture_handlers:{selector}', 'options': {}}
    installation['value']['responses']['run'] = encoded_reply(raw=b'actual output', code=9, error=b'actual error')
    value = report(invoke(installation, 'run'), code)
    expected = json.loads((FIXTURES / 'plugin.expected.json').read_text())
    expected.update(exit_code=code, selected=selector)
    assert value == expected
    assert [r['role'] for r in call_log(installation)] == ['run']


@pytest.mark.parametrize('scenario,expected_roles,ready', [
    ('success', ['infra'], True),
    ('unknown', ['infra'], False),
    ('contradiction', ['infra'], False),
    ('disabled', ['infra'], False),
    ('repair-success', ['infra', 'repair', 'verify'], True),
    ('repair-failure', ['infra', 'repair', 'verify'], False),
    ('verify-failure', ['infra', 'repair', 'verify'], False),
    ('many-problems', ['infra', 'repair', 'verify'], False),
])
def test_actual_recovery_actions_and_all_attempted_diagnostics(installation, scenario, expected_roles, ready):
    responses = installation['value']['responses']
    if scenario != 'success':
        responses['infra'] = encoded_reply('failed', 1, error=b'initial diagnostic')
    if scenario == 'unknown':
        responses['infra'] = encoded_reply(raw=b'unknown tool reply', code=8, error=b'unknown diagnostic')
    if scenario == 'contradiction':
        responses['infra'] = encoded_reply('succeeded', 9)
    if scenario == 'disabled':
        installation['command']['handler']['options']['repair_operations'] = []
    if scenario == 'repair-failure':
        responses['repair'] = encoded_reply('failed', 1, error=b'repair diagnostic')
    if scenario in ('verify-failure', 'many-problems'):
        responses['verify'] = encoded_reply('failed', 1, error=b'second diagnostic')
    if scenario == 'many-problems':
        responses['infra'] = encoded_reply(raw=b'{"status":"failed","errors":["first","second"]}', code=1)
    value = report(invoke(installation), 0 if ready else 4)
    assert value['status'] == ('ready' if ready else 'error')
    assert [r['role'] for r in call_log(installation)] == expected_roles
    assert value['actions'] == ([] if len(expected_roles) == 1 else ['repair', 'verify'])
    stages = ['initial'] if len(expected_roles) == 1 else ['initial', 'repair', 'verification']
    expected = [{'stage': stage,
        'stdout': base64.b64decode(responses[role]['stdout']).decode(),
        'stderr': base64.b64decode(responses[role]['stderr']).decode(),
        'exit_code': responses[role]['exit_code']} for stage, role in zip(stages, expected_roles)]
    assert value['observations'] == expected


def test_large_binary_diagnostics_remain_complete_without_recovery(installation):
    body = (FIXTURES / 'large.stdout.bin').read_bytes()
    errors = (FIXTURES / 'large.stderr.bin').read_bytes()
    installation['value']['responses']['infra'] = encoded_reply(raw=body, code=7, error=errors)
    value = report(invoke(installation), 4)
    assert value['observations'] == json.loads((FIXTURES / 'large.expected.json').read_text())
    assert [r['role'] for r in call_log(installation)] == ['infra']


def test_missing_recovery_setting_fails_before_a_repair_effect(installation):
    installation['value']['responses']['infra'] = encoded_reply('failed', 1)
    del installation['command']['handler']['options']['verification_command']
    value = report(invoke(installation), 4)
    assert value['status'] == 'error'
    assert 'verification_command' in value['reason']
    assert [r['role'] for r in call_log(installation)] == ['infra']
