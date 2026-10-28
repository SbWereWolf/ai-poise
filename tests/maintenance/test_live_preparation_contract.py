"""V01: modern configured public preparation with an independent external oracle."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from batch.helpers import request
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/live_deployment'


@pytest.fixture(params=['poise-op', 'erp'])
def modern_case(project, request, monkeypatch):
    name = request.param
    operator = project['root'] / 'operator'
    operator.mkdir()
    shutil.copyfile(FIXTURES / 'prepare.sh', operator / 'prepare.sh')
    selected = operator / (name + '-values.json')
    write_json(selected, {'fail_check': False, 'consumer': name})
    entry = operator / 'selected-external-tool'
    shutil.copyfile(FIXTURES / 'entry.sh', entry)
    entry.chmod(0o700)
    monkeypatch.setenv('LIVE_CONTRACT_PYTHON', sys.executable)
    monkeypatch.setenv('LIVE_CONTRACT_FIXTURE', str(FIXTURES / 'protocol_fixture.py'))
    data = json.loads((ROOT / 'config/worktree-preparation' / (name + '.example.json')).read_text())
    parameters = dict(source_root=str(FIXTURES), python=sys.executable,
        start_app=str(entry), operator=str(operator), selected_values=str(selected))
    data['parameters'].update(parameters)
    configuration = write_json(operator / 'preparation.json', data)
    project['cfg']['worktree_preparation'] = {'configuration': str(configuration)}
    project['cfg']['paths']['worktrees'] = str(project['app'] / 'build/worktrees')
    write_json(project['config_path'], project['cfg'])
    (project['app'] / '.gitignore').write_text('/build/\n/.runtime/\n/platform-infrastructure/\n')
    git(project['app'], 'add', '.gitignore')
    git(project['app'], 'commit', '-m', 'Declare fixture working outputs')
    runtime = WorkPoise(project['config_path'], 'LIVE-CONTRACT-FIXTURE')
    return dict(project=project, tools=WorkTools(runtime), selected=selected,
        runtime=runtime, name=name, base=git(project['app'], 'rev-parse', 'HEAD'),
        configuration=configuration, parameters=parameters)


def bootstrap(case, *, again=False, request_id=None):
    body = {'task': {'id': case['project']['task']['id']} if again
            else deepcopy(case['project']['task']),
            'decision': None, 'feedback': None, 'rework_stage': None}
    if request_id:
        body['preparation_request_id'] = request_id
    return case['tools'].invoke(request('bootstrap', body))


def report(reply):
    path = Path(reply['preparation']['receipt_path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reply['preparation']['receipt_sha256']
    return json.loads(path.read_text())


def events(receipt):
    path = Path(receipt['task_root']) / 'events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize('fail_check', [False, True])
def test_fixture_control_is_healthy_before_product_red(modern_case, fail_check):
    control = json.loads((FIXTURES / 'modern-preparation-control.json').read_text())
    control['parameters'] = modern_case['parameters']
    write_json(modern_case['configuration'], control)
    write_json(modern_case['selected'], {'fail_check': fail_check,
                                       'consumer': modern_case['name']})
    reply = bootstrap(modern_case)
    receipt = report(reply)
    assert reply['preparation']['status'] == ('error' if fail_check else 'ready'), receipt
    assert events(receipt), 'The independent command fixture must actually execute'


def test_isolated_application_fixture_uses_its_public_project_owner(modern_case):
    from live_application_support import configured_private_project

    case = modern_case
    control = json.loads((FIXTURES / 'modern-preparation-control.json').read_text())
    control['parameters'] = case['parameters']
    write_json(case['configuration'], control)
    git(case['project']['app'], 'branch', 'codex/deployment-source')
    tools, task, config = configured_private_project(case['project'], case['name'],
        case['project']['app'], case['configuration'])
    reply = tools.invoke(request('bootstrap', {'task': task, 'decision': None,
        'feedback': None, 'rework_stage': None}))
    assert reply['preparation']['status'] == 'ready', report(reply)
    assert report(reply)['project_id'] == case['name']
    assert config['project'] == case['name']


def test_modern_preparation_confirms_real_worktree_and_each_operation(modern_case):
    reply = bootstrap(modern_case)
    receipt = report(reply)
    assert reply['preparation']['status'] == 'ready', receipt
    expected = ['infra', 'deps'] + (['adjust'] if modern_case['name'] == 'erp' else []) + ['check', 'check']
    observed = events(receipt)
    assert [row['operation'] for row in observed] == expected
    root = Path(reply['worktree'])
    assert root.is_relative_to(modern_case['project']['app'])
    assert git(root, 'rev-parse', 'HEAD') == modern_case['base']
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'tasks/T1'
    assert all(row['base_dir'] == str(root) and row['cwd'] == str(root)
               and row['project'] == 'demo' and row['base'] == modern_case['base']
               for row in observed)


def test_preparation_preserves_user_bytes_and_previous_receipt(modern_case):
    first = bootstrap(modern_case)
    assert first['preparation']['status'] == 'ready', report(first)
    old_path = Path(first['preparation']['receipt_path'])
    old_bytes = old_path.read_bytes()
    sentinel = Path(first['worktree']) / 'build/user-data.bin'
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b'user data\x00preserved')
    second = bootstrap(modern_case, again=True, request_id='repeat-modern-preparation')
    assert second['preparation']['status'] == 'ready', report(second)
    assert second['worktree'] == first['worktree']
    assert second['preparation']['attempt_id'] != first['preparation']['attempt_id']
    assert old_path.read_bytes() == old_bytes
    assert sentinel.read_bytes() == b'user data\x00preserved'


def test_failed_availability_never_becomes_ready(modern_case):
    write_json(modern_case['selected'], {'fail_check': True, 'consumer': modern_case['name']})
    reply = bootstrap(modern_case)
    receipt = report(reply)
    assert reply['preparation']['status'] == 'error'
    assert receipt['status'] == 'error'
    raw = [command for operation in receipt['operations'] for command in operation['commands']]
    assert any(b'Independent resource availability check failed' in Path(c['stdout_path']).read_bytes()
        or b'Independent resource availability check failed' in Path(c['stderr_path']).read_bytes()
        for c in raw)
