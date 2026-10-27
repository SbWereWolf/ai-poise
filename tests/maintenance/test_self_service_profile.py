"""The shipped Poise profile against the real selected SDK and private stores."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import venv

import pytest
from test_self_service_boundary import encoded_reply


ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'config/maintenance/start-app/profile.example.json'
FIXTURES = Path(__file__).parent / 'fixtures/self_service'


def command(argv, *, cwd, env=None):
    return subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True)


def succeeded(reply):
    assert reply.returncode == 0, (reply.stdout, reply.stderr)
    return json.loads(reply.stdout)


@pytest.fixture(params=['first instance', 'второй экземпляр'])
def serviced(tmp_path, request):
    sdk = Path(os.environ['ENV2_SELF_SERVICE_SDK_PYTHON'])
    wheelhouse = Path(os.environ['ENV2_SELF_SERVICE_WHEELHOUSE'])
    assert sdk.is_file() and wheelhouse.is_dir(), 'SETUP: select installed SDK and real local dependencies'
    spec = ROOT / PROFILE
    if not spec.is_file():
        return {'missing_profile': str(spec)}
    root = tmp_path / request.param
    root.mkdir()
    # Git supplies paths, but bytes come from the current working files. This
    # includes new source files without copying ignored stores or old HEAD bytes.
    selected_files = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    for relative in selected_files.decode().split('\x00'):
        if not relative or not (ROOT / relative).is_file():
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for arguments in (['init', '-b', 'master'], ['config', 'user.name', 'Fixture'],
                      ['config', 'user.email', 'fixture@example.invalid'],
                      ['add', '--all'],
                      ['commit', '-m', 'Independent serviced source']):
        result = command(['git', *arguments], cwd=root)
        assert result.returncode == 0, ('SETUP: private repository', result.stderr)
    venv.EnvBuilder(with_pip=True).create(root / '.venv')
    python = root / '.venv/bin/python'
    profile = root / 'operator/profile'
    profile.parent.mkdir()
    state = tmp_path / (request.param + ' data')
    module_path = os.pathsep.join([str(root / 'src'), str(root / '.runtime/dependencies'), str(root)])
    environment = dict(os.environ, PYTHONPATH=module_path, PYTHONDONTWRITEBYTECODE='1',
                       PIP_NO_INDEX='1', PIP_FIND_LINKS=str(wheelhouse))
    published = command([python, '-P', '-s', '-B', '-m', 'poise_environment.profile',
        '--source', root, '--profile-spec', root / PROFILE, '--output', profile,
        '--state', state, '--repository', root, '--project', 'isolated',
        '--base-ref', 'master', '--author-name', 'Fixture',
        '--author-email', 'fixture@example.invalid', '--module-path', module_path],
        cwd=root, env=environment)
    assert succeeded(published)['state_created'] is False
    assert not state.exists()
    bootstrap = json.loads((profile / 'bootstrap.json').read_text())
    bootstrap['parameters']['python'] = str(sdk)
    (profile / 'bootstrap.json').write_text(json.dumps(bootstrap))
    values = json.loads((profile / 'parameters.json').read_text())
    values.update(wheelhouse=str(wheelhouse), dependency_target=str(root / '.runtime/dependencies'))
    (profile / 'parameters.json').write_text(json.dumps(values))
    sentinel = tmp_path / 'unrelated.txt'
    sentinel.write_bytes(b'preserve independent data\x00')
    return dict(root=root, python=python, profile=profile, state=state,
                env=environment, sentinel=sentinel)


def invoke(case, operation):
    return command([case['python'], '-I', '-B', case['root'] / 'bootstrap', operation,
        '--config', case['profile'] / 'bootstrap.json', '--base-dir', case['root']],
        cwd=case['root'], env=case['env'])


def check_ready(reply):
    value = succeeded(reply)
    assert value['status'] == 'ready'
    return value


def test_actual_separate_preparation_checks_dependency_and_launch(serviced):
    case = serviced
    assert 'missing_profile' not in case, 'POISE_SDK_PROFILE_MISSING'
    before = hashlib.sha256(case['sentinel'].read_bytes()).hexdigest()
    for operation in ('infra', 'deps', 'check'):
        check_ready(invoke(case, operation))
    config = case['profile'] / 'selected-project/project.json'
    assert config.is_file()
    selected = json.loads(config.read_text())
    assert Path(selected['paths']['state']) == case['state']
    database = Path(selected['paths']['database'])
    database = database if database.is_absolute() else case['state'] / database
    assert database.is_file()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT id FROM tasks ORDER BY id').fetchall() == []
        assert db.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',
                          ('table', 'tasks')).fetchall() == [('tasks',)]
    dep = command([case['python'], '-P', '-s', '-B', '-c',
                   'import psutil; print(psutil.__file__); print(psutil.Process().pid > 0)'],
                  cwd=case['root'], env=case['env'])
    assert dep.returncode == 0, dep.stderr
    origin, working = dep.stdout.decode().splitlines()
    assert Path(origin).is_relative_to(case['root'] / '.runtime/dependencies')
    assert working == 'True'
    launched = check_ready(invoke(case, 'run'))
    assert len(launched['observations']) == 1
    raw = json.loads(launched['observations'][0]['stdout'])
    assert raw['operation'] == 'run' and raw['status'] == 'succeeded'
    assert len(raw['requirements']) == 1
    child = raw['requirements'][0]['result']['commands'][0]
    import base64
    started = json.loads(base64.b64decode(child['stdout']['data']))
    assert started['projects'] == [{'project': 'isolated', 'config_path': str(config)}]
    assert started['errors'] == []
    for operation in ('infra', 'deps', 'check'):
        check_ready(invoke(case, operation))
    assert hashlib.sha256(case['sentinel'].read_bytes()).hexdigest() == before


def test_check_reports_absent_resources_without_creating_them(serviced):
    case = serviced
    assert 'missing_profile' not in case, 'POISE_SDK_PROFILE_MISSING'
    reply = invoke(case, 'check')
    assert reply.returncode != 0
    value = json.loads(reply.stdout)
    assert value['status'] == 'error'
    assert value['actions'] == []
    assert not case['state'].exists()
    raw = json.loads(value['observations'][0]['stdout'])
    assert raw['status'] == 'failed'
    assert any(resource['presence']['status'] == 'failed' for resource in raw['resources'])


def test_run_does_not_prepare_missing_resources(serviced):
    case = serviced
    assert 'missing_profile' not in case, 'POISE_SDK_PROFILE_MISSING'
    before = {str(p.relative_to(case['profile'])): p.read_bytes()
              for p in case['profile'].rglob('*') if p.is_file()}
    result = invoke(case, 'run')
    assert result.returncode != 0
    value = json.loads(result.stdout)
    assert value['status'] == 'error' and value['actions'] == []
    assert not case['state'].exists()
    assert {str(p.relative_to(case['profile'])): p.read_bytes()
            for p in case['profile'].rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('scenario', ['availability', 'coherence', 'not-configured'])
def test_actual_sdk_reports_declared_availability_and_coherence(serviced, scenario):
    case = serviced
    assert 'missing_profile' not in case, 'POISE_SDK_PROFILE_MISSING'
    shutil.copy2(FIXTURES / 'handlers.py', case['root'] / 'fixture_handlers.py')
    spec = case['profile'] / 'probe.json'
    responses = {'presence': encoded_reply(), 'availability': encoded_reply(),
                 'coherence': encoded_reply()}
    if scenario != 'not-configured':
        responses[scenario] = encoded_reply('failed', 0)
    spec.write_text(json.dumps({'log': str(case['profile'] / 'probe.log'), 'responses': responses}))

    def action(role):
        return {'commands': [{'argv': [str(case['python']), '-I', '-B',
            str(FIXTURES / 'command.py'), role, str(spec), str(case['root'])],
            'cwd': str(case['root']), 'env': {},
            'interpreter': {'name': 'probe', 'options': {'accept': [0], 'statuses': ['succeeded']}}}]}

    app_path = case['profile'] / 'app.json'
    app = json.loads(app_path.read_text())
    app['interpreters'] = {'probe': {'module': 'fixture_handlers', 'attribute': 'status_result'}}
    app_path.write_text(json.dumps(app))
    checks = {'presence': action('presence')}
    if scenario == 'availability':
        checks['availability'] = action('availability')
    resource = {'id': 'selected-resource', 'commands': action('presence')['commands'], 'checks': checks}
    for operation, items in [('infra', [resource]), ('deps', []),
                             ('check', [] if scenario == 'not-configured'
                              else [{'id': 'selected-coherence', **action('coherence')}])]:
        path = case['profile'] / app['operations'][operation]
        path.write_text(json.dumps({'requirements': items}))
    reply = invoke(case, 'check')
    value = succeeded(reply) if scenario == 'not-configured' else json.loads(reply.stdout)
    assert reply.returncode == (0 if scenario == 'not-configured' else 4)
    assert value['status'] == ('ready' if scenario == 'not-configured' else 'error')
    raw = json.loads(value['observations'][0]['stdout'])
    assert raw['resources'][0]['id'] == 'selected-resource'
    expected_availability = 'failed' if scenario == 'availability' else 'not_configured'
    assert raw['resources'][0]['availability']['status'] == expected_availability
    if scenario != 'not-configured':
        assert raw['checks'][0]['id'] == 'selected-coherence'
        assert raw['checks'][0]['result']['status'] == ('failed' if scenario == 'coherence' else 'succeeded')
    assert not case['state'].exists()
