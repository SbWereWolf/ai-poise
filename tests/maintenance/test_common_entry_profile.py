"""Poise declarations through the installed common CLI, not a project adapter."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / 'fixtures/common_entry'


@pytest.fixture(autouse=True)
def selected_poise_tool_source(monkeypatch):
    # Publication/setup uses the selected application, while ordinary SDK calls
    # below receive no Poise source/import bootstrap from the invoking agent.
    monkeypatch.setenv('POISE_ROOT', str(ROOT))


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def call(label, argv, cwd, env, *, packet=None):
    result = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                            input=None if packet is None else json.dumps(packet).encode(),
                            capture_output=True)
    identity = Path(cwd).name + '-' + hashlib.sha256(str(cwd).encode()).hexdigest()[:10]
    captures = Path(os.environ['POISE_COMMON_CAPTURE_ROOT']) / identity
    captures.mkdir(parents=True, exist_ok=True)
    (captures / (label + '.stdout')).write_bytes(result.stdout)
    (captures / (label + '.stderr')).write_bytes(result.stderr)
    write(captures / (label + '.json'), dict(argv=[str(a) for a in argv],
          cwd=str(cwd), stdin=packet, exit_code=result.returncode,
          stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
          stderr_sha256=hashlib.sha256(result.stderr).hexdigest()))
    return result


def success(reply):
    assert reply.returncode == 0, (reply.stdout, reply.stderr)
    return json.loads(reply.stdout)


def sdk_environment():
    entry = Path(os.environ['POISE_COMMON_START_APP'])
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env.pop('POISE_ROOT', None)
    env['PATH'] = str(entry.parent) + os.pathsep + env['PATH']
    version = subprocess.run([str(entry), '--version'], env=env, capture_output=True)
    assert version.returncode == 0 and version.stdout == b'environment-maintenance 3.0.0\n', (
        'SETUP: select the already accepted installed common package', version.stdout, version.stderr)
    return entry, env


@pytest.fixture(params=['first instance', 'второй экземпляр'])
def installation(tmp_path, request):
    root = tmp_path / request.param
    cloned = subprocess.run(['git', 'clone', '--local', '--no-hardlinks',
                             str(ROOT), str(root)], capture_output=True)
    assert cloned.returncode == 0, ('SETUP: private serviced Git repository', cloned.stderr)
    branch = subprocess.run(['git', '-C', str(root), 'branch', '-m', 'master'], capture_output=True)
    assert branch.returncode == 0, ('SETUP: private fixture branch', branch.stderr)
    # Include the current candidate's working bytes, as the existing real
    # self-service fixture does. HEAD alone would miss uncommitted config edits.
    files = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    for relative in files.decode().split('\x00'):
        if not relative:
            continue
        source, destination = ROOT / relative, root / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.is_file():
            destination.unlink()
    for argv in (['add', '--all'], ['-c', 'user.name=Fixture', '-c',
                 'user.email=fixture@example.invalid', 'commit', '--allow-empty',
                 '-m', 'Current candidate fixture']):
        snapshot = subprocess.run(['git', '-C', str(root), *argv], capture_output=True)
        assert snapshot.returncode == 0, ('SETUP: private source snapshot', snapshot.stderr)
    state = tmp_path / 'application-data'
    module_path = os.pathsep.join([str(ROOT / 'src'), str(root / '.runtime/dependencies'), str(ROOT)])
    env = dict(os.environ, PYTHONPATH=module_path, POISE_ROOT=str(ROOT),
               PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1',
               PIP_FIND_LINKS=os.environ['ENV2_SELF_SERVICE_WHEELHOUSE'])
    sentinel = tmp_path / 'foreign-sentinel.bin'
    sentinel.write_bytes(b'foreign data\x00must remain\n')
    return dict(root=root, source=ROOT, python=Path(sys.executable),
                profile=tmp_path / 'profile', state=state, env=env, sentinel=sentinel)


def deploy(case, tmp_path):
    entry, clean = sdk_environment()
    published = call('publish-declared-profile', [case['python'], '-P', '-s', '-B',
        '-m', 'poise_environment.profile', '--source', case['source'], '--profile-spec',
        ROOT / 'config/maintenance/start-app/profile.example.json', '--output', case['profile'],
        '--state', case['state'], '--repository', case['root'], '--project', 'isolated',
        '--base-ref', 'master', '--author-name', 'Fixture', '--author-email',
        'fixture@example.invalid', '--module-path', case['env']['PYTHONPATH']],
        case['root'], case['env'])
    if (published.returncode == 2 and
            json.loads(published.stdout) == {'status': 'invalid', 'error': "'POISE_ROOT'"}):
        print('DECLARED_PROFILE_UNBOUND_ROOT:' + case['root'].name)
    assert published.returncode == 0, ('DECLARED_PROFILE_COULD_NOT_PUBLISH',
                                      published.stdout, published.stderr)
    assert json.loads(published.stdout)['state_created'] is False
    setup = case['profile'] / 'setup.json'
    creation = dict(schema='project-setup-1', request_id='common-entry-project-create',
                    destination='selected-project', template={'id': 'common'},
                    edits=read(case['profile'] / 'project-edits.json'), probe_repository=False)
    created = success(call('public-project-create',
        [sys.executable, '-B', '-m', 'poise', 'project', '--settings', setup],
        case['root'], case['env'], packet=creation))
    request = dict(schema='project-config-update-1', request_id='common-entry-adjust',
                   config_path=created['config_path'], expected_revision=created['revision'],
                   manifest_edits=[{'path': ['git', 'author_name'], 'value': 'Fixture'}],
                   workflow_updates=[], state_relocation=None, probe_repository=False,
                   receipt_path='operations/common-entry-adjust.json')
    update = tmp_path / 'adjust-request.json'
    write(update, request)
    operator = tmp_path / 'operator'
    operator.mkdir()
    for source, destination in (
        ('poise-op-supervisor.example.json', 'app.json'),
        ('poise-op-supervisor-infra.example.json', 'poise-op-supervisor-infra.json')):
        shutil.copy2(ROOT / 'config/worktree-preparation' / source, operator / destination)
    write(operator / 'empty.json', {'requirements': []})
    parameters = dict(source_root=str(case['source']), python=str(case['python']),
        start_app=str(entry), sdk_python=os.environ['ENV2_SELF_SERVICE_SDK_PYTHON'],
        profile_root=str(case['profile']), state_root=str(case['state']),
        repository=str(case['root']), project_id='isolated', base_ref='master',
        author_name='Fixture', author_email='fixture@example.invalid',
        module_path=case['env']['PYTHONPATH'], runtime_module_path=case['env']['PYTHONPATH'],
        path=clean['PATH'], wheelhouse=os.environ['ENV2_SELF_SERVICE_WHEELHOUSE'],
        publisher=str(case['source'] / 'src/poise_environment/worktree_profile.py'),
        profile_spec=str(case['source'] / 'config/maintenance/start-app/profile.example.json'),
        patch_spec=str(case['source'] / 'config/worktree-preparation/poise-sdk-binding.example.json'),
        adjust_request=str(update), task_root=str(tmp_path))
    values = operator / 'values.json'
    parameters['supervisor_parameters'] = str(values)
    write(values, parameters)
    # The production declaration owns publication and preparation, including the
    # root main. The test never manufactures a launcher or a substitute main.
    prepared = success(call('declared-deployment', [entry, 'infra', '--config',
        operator / 'app.json', '--parameters', values, '--base-dir', case['root'],
        '--requirements', 'all'], case['root'], case['env']))
    assert prepared['status'] == 'succeeded', ('SETUP: existing deployment failed', prepared)
    selected = read(case['profile'] / 'parameters.json')
    selected['adjust_request'] = str(update)
    write(case['profile'] / 'parameters.json', selected)
    return dict(case, entry=entry, clean_env=clean, adjust_request=update,
                initial_revision=created['revision'], selected_config=Path(created['config_path']))


def ordinary(case, label, operation, *overrides, cwd=None):
    return call(label, ['start-app', operation, *overrides], cwd or case['root'], case['clean_env'])


def input_hashes(case):
    paths = [case['root'] / 'start-app.json', case['adjust_request']]
    paths.extend(case['profile'] / name for name in
                 ('parameters.json', 'infra.json', 'deps.json', 'adjust.json', 'check.json', 'run.json'))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def assert_project_result(value, identifier):
    assert value['status'] == 'succeeded', value
    raw = value['requirements'][0]['result']['commands'][0]['stdout']
    packet = json.loads(base64.b64decode(raw['data']))
    assert [p['project'] for p in packet['projects']] == [identifier]
    assert packet['errors'] == []


def test_declared_deployment_and_five_common_commands_preserve_context(installation, tmp_path):
    case = deploy(installation, tmp_path)
    before_sentinel = case['sentinel'].read_bytes()
    for operation in ('infra', 'deps', 'adjust', 'check', 'run'):
        value = success(ordinary(case, 'ordinary-' + operation, operation))
        assert value['status'] == 'succeeded', value
    before = input_hashes(case)
    configuration = read(case['selected_config'])
    assert configuration['git']['repository'] == str(case['root'])
    assert Path(configuration['paths']['state']) == case['state']
    assert (case['state'] / configuration['paths']['database']).is_file()
    assert list((case['root'] / '.runtime/dependencies').glob('psutil*'))
    ignored = subprocess.run(['git', '-C', str(case['root']), 'check-ignore',
                              '--no-index', 'start-app.json'], capture_output=True)
    assert ignored.returncode == 0, 'Working root main must be ignored by Git'
    assert subprocess.check_output(['git', '-C', str(case['root']), 'ls-files',
                                    '--', 'start-app.json']) == b''
    assert_project_result(success(ordinary(case, 'default-run', 'run')), 'isolated')
    foreign = tmp_path / 'foreign-cwd'
    foreign.mkdir()
    assert_project_result(success(ordinary(case, 'foreign-explicit', 'run', '--config',
                          str(case['root'] / 'start-app.json'), cwd=foreign)), 'isolated')
    # An independently created public project supplies a distinguishable run
    # context; generic argument priority is already proven by the SDK tests.
    other_profile = tmp_path / 'other-profile'
    other_profile.mkdir()
    other_settings = read(case['profile'] / 'setup.json')
    other_settings['root'] = str(other_profile)
    other_settings['registry'] = 'configured-projects.json'
    write(other_profile / 'setup.json', other_settings)
    for relative in ('config', 'project'):
        origin = case['profile'] / relative
        if origin.exists():
            shutil.copytree(origin, other_profile / relative)
    edits = read(case['profile'] / 'project-edits.json')
    for edit in edits:
        if edit['path'] == ['project']:
            edit['value'] = 'other-isolated'
        if edit['path'] == ['paths', 'state']:
            edit['value'] = str(tmp_path / 'other-state')
    packet = dict(schema='project-setup-1', request_id='other-project-create',
        destination='selected-project', template={'id': 'common'}, edits=edits, probe_repository=False)
    success(call('create-other', [sys.executable, '-B', '-m', 'poise', 'project', '--settings',
        other_profile / 'setup.json'], case['root'], case['env'], packet=packet))
    other_values = read(case['profile'] / 'parameters.json')
    other_values['profile_root'] = str(other_profile)
    alternate = case['root'] / 'alternate-parameters.json'
    write(alternate, other_values)
    other_before = {str(p): p.read_bytes() for p in other_profile.rglob('*.json')}
    assert_project_result(success(ordinary(case, 'override-run', 'run', '--parameters',
                                         'alternate-parameters.json')), 'other-isolated')
    assert_project_result(success(ordinary(case, 'ordinary-after-override', 'run')), 'isolated')
    assert input_hashes(case) == before
    assert {str(p): p.read_bytes() for p in other_profile.rglob('*.json')} == other_before
    assert case['sentinel'].read_bytes() == before_sentinel


def test_adjust_applies_declared_existing_setting_through_public_owner(installation, tmp_path):
    case = deploy(installation, tmp_path)
    assert success(ordinary(case, 'initial-adjust', 'adjust'))['status'] == 'succeeded'
    previous = read(case['selected_config'])
    request = read(case['adjust_request'])
    receipt = read(case['profile'] / request['receipt_path'])
    # The native owner receipt is the authority for a new configuration intent.
    previous_result = receipt.get('result', receipt)
    request.update(request_id='common-entry-align-author',
                   expected_revision=previous_result['after_revision'],
                   receipt_path='operations/common-entry-align-author.json')
    request['manifest_edits'] = [{'path': ['git', 'author_name'], 'value': 'Aligned author'}]
    write(case['adjust_request'], request)
    edits_path = case['profile'] / 'project-edits.json'
    edits = read(edits_path)
    for edit in edits:
        if edit['path'] == ['git', 'author_name']:
            edit['value'] = 'Aligned author'
    write(edits_path, edits)
    before = input_hashes(case)
    drift = ordinary(case, 'observed-drift', 'check')
    assert drift.returncode != 0 and json.loads(drift.stdout)['status'] == 'failed'
    assert success(ordinary(case, 'apply-alignment', 'adjust'))['status'] == 'succeeded'
    expected = deepcopy(previous)
    expected['git']['author_name'] = 'Aligned author'
    assert read(case['selected_config']) == expected
    assert success(ordinary(case, 'aligned-check', 'check'))['status'] == 'succeeded'
    assert success(ordinary(case, 'repeat-alignment', 'adjust'))['status'] == 'succeeded'
    assert read(case['selected_config']) == expected
    assert input_hashes(case) == before


CASES = read(DATA / 'responses.json')


@pytest.mark.parametrize('sample', CASES, ids=[row['id'] for row in CASES])
def test_shipped_command_predicates_preserve_real_failures(sample, tmp_path):
    entry, env = sdk_environment()
    if sample['subject'] == 'project-list':
        action = read(ROOT / 'config/maintenance/start-app/run.example.json')['requirements'][0]
    else:
        resource = read(ROOT / 'config/maintenance/start-app/infra.example.json')['requirements'][0]
        action = resource if sample['subject'] == 'outcome' else resource['checks']['presence']
    action = deepcopy(action)
    action['id'] = 'selected-predicate'
    selected = action['commands'][0]
    selected['env']['COMMON_LEAF_CASE'] = json.dumps(sample)
    selected['env']['COMMON_LEAF_STDERR'] = str(
        Path(__file__).parent / 'fixtures/self_service/large.stderr.bin')
    later = tmp_path / 'later.marker'
    action['commands'].append(dict(argv=[sys.executable, str(DATA / 'leaf.py'), '--marker', str(later)],
        cwd=str(tmp_path), env={}, interpreter={'name': 'exit', 'options': {'accept': [0]}}))
    write(tmp_path / 'operation.json', {'requirements': [action]})
    # Subject: shipped Poise command/predicate. Common transport/plugin is the
    # independently accepted package, not another Poise parser.
    plugin = {'module': 'interpreters', 'attribute': 'exit_result'}
    write(tmp_path / 'main.json', {'operations': {'run': 'operation.json'},
        'interpreters': {name: plugin for name in ('resource', 'project-list', 'exit')}})
    parameters = read(ROOT / 'config/maintenance/start-app/profile.example.json')['parameters']
    parameters.update(python=str(DATA / 'leaf.py'), repository=str(tmp_path),
        profile_root=str(tmp_path), module_path=str(ROOT / 'src'),
        runtime_module_path=str(ROOT / 'src'), base_ref='master',
        jq='/usr/bin/jq', bash='/bin/bash')
    write(tmp_path / 'values.json', parameters)
    reply = call(sample['id'], [entry, 'run', '--config', tmp_path / 'main.json',
        '--parameters', tmp_path / 'values.json', '--base-dir', tmp_path, '--requirements', 'all'],
        tmp_path, env)
    value = json.loads(reply.stdout)
    if (not sample['success'] and reply.returncode == 0 and
            value['status'] == 'succeeded' and later.exists()):
        print('UNVALIDATED_RESPONSE:' + sample['id'])
    assert (reply.returncode == 0) == sample['success'], (reply.stdout, reply.stderr)
    assert value['status'] == ('succeeded' if sample['success'] else 'failed')
    assert later.exists() == sample['success']
    command = value['requirements'][0]['result']['commands'][0]
    stderr = base64.b64decode(command['stderr']['data'])
    assert (Path(__file__).parent / 'fixtures/self_service/large.stderr.bin').read_bytes() in stderr
    if sample['success']:
        assert json.loads(base64.b64decode(command['stdout']['data'])) == json.loads(sample['stdout'])
    else:
        assert base64.b64decode(command['stdout']['data']) == sample['stdout'].encode()
