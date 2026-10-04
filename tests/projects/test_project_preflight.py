"""Public project diagnosis: independent packets, real stores and local Git."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from poise.interfaces.projects import execute
from tests.conftest import write_json
from .helpers import setup_case


FIXTURES = Path(__file__).parent / 'fixtures' / 'project_preflight'
SOURCE = str((Path(__file__).resolve().parents[2] / 'src').resolve())


def fixture(name, values):
    """Substitute independently arranged paths; never serialize production results."""
    def replace(value):
        if isinstance(value, str) and value.startswith('$'):
            return values[value[1:]]
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value
    return replace(json.loads((FIXTURES / name).read_text(encoding='utf-8')))


def snapshot(root):
    """Every fixture entry, including absence, symlinks, dirs and Git internals."""
    entries = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        value = os.readlink(path) if stat.S_ISLNK(info.st_mode) else (
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
        entries[str(path.relative_to(root))] = (
            info.st_mode, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, value)
    return entries


@pytest.fixture
def case(project):
    settings, _, _ = setup_case(project)
    raw = json.loads(settings.read_text())
    raw['exit_codes'] = {'success': 17, 'rejected': 23, 'aborted': 29}
    write_json(settings, raw)
    cfg = deepcopy(project['cfg'])
    cfg['git']['commit_pattern'] = r'Change\n\nReason'
    write_json(project['config_path'], cfg)
    project['cfg'] = cfg
    root = project['root']
    values = {
        'source': SOURCE,
        'settings': str(settings),
        'config': str(project['config_path']),
        'repository': str(project['app']),
        'state': str(root / 'state'),
        'task_db': str(root / 'state/state.sqlite'),
        'task_lock': str(root / 'state/state.lock'),
        'requirements_db': str(root / 'state/requirements.sqlite'),
        'requirements_lock': str(root / 'state/requirements.lock'),
    }
    return project, settings, cfg, values


def invoke(case, request):
    project, settings, _, _ = case
    before = snapshot(project['root'].parent)
    output = io.StringIO()
    code = execute(settings, io.BytesIO(json.dumps(request).encode()), output, action='check')
    # Assert preservation before protocol: even an incorrect refusal must be read-only.
    assert snapshot(project['root'].parent) == before
    packet = json.loads(output.getvalue())
    assert set(packet) == {'schema', 'status', 'ready', 'reason', 'context', 'checks', 'recovery'}
    return code, packet


def request(case):
    return fixture('request.json', case[3])


def expected(case, *, statuses=None, reason=None, status='checked', ready=True):
    result = fixture('success.json', case[3])
    result.update(status=status, ready=ready, reason=reason)
    if statuses is not None:
        result['checks'] = [
            {'name': name, 'status': state, 'reason': cause}
            for name, state, cause in statuses]
    if not ready:
        result['recovery'] = fixture('recovery.json', case[3])
    return result


def test_check_accepts_separated_installation_repository_and_relative_requirements(case):
    assert case[3]['source'] != case[3]['repository']
    code, actual = invoke(case, request(case))
    assert code == 17
    assert actual == expected(case)


def test_absolute_requirements_paths_are_not_rebased(case):
    project, _, cfg, values = case
    external = project['root'].parent / 'private-requirements'
    external.mkdir()
    cfg['paths']['requirements_database'] = str(external / 'requirements.sqlite')
    cfg['paths']['requirements_lock'] = str(external / 'requirements.lock')
    write_json(project['config_path'], cfg)
    values['requirements_db'] = str(external / 'requirements.sqlite')
    values['requirements_lock'] = str(external / 'requirements.lock')
    code, actual = invoke(case, request(case))
    assert (code, actual) == (17, expected(case))
    assert list(external.iterdir()) == []


@pytest.mark.parametrize('probe,message,names', [
    (False, 'Change\n\nReason', 'git'),
    (True, None, 'commit_policy'),
    (False, None, 'git, commit_policy'),
])
def test_mandatory_skip_is_checked_but_never_ready(case, probe, message, names):
    body = request(case)
    body.update(probe_repository=probe, commit_message=message)
    reason = f'Обязательные проверки не выполнены: {names}.'
    wanted = expected(case, ready=False, reason=reason, statuses=[
        ('configuration', 'passed', None),
        ('git', 'passed' if probe else 'not_checked', None),
        ('commit_policy', 'passed' if message is not None else 'not_checked', None),
        ('task_lookup', 'not_checked', None),
    ])
    assert invoke(case, body) == (23, wanted)


@pytest.mark.parametrize('probe', [True, False])
def test_commit_policy_refusal_preserves_cause_and_precedes_lookup(case, probe):
    body = request(case)
    body.update(probe_repository=probe, commit_message='Change\\n\\nReason', task_id='NOT-HERE')
    reason = 'Сообщение коммита не соответствует правилу проекта'
    wanted = expected(case, status='rejected', ready=False, reason=reason, statuses=[
        ('configuration', 'passed', None),
        ('git', 'passed' if probe else 'not_checked', None),
        ('commit_policy', 'rejected', reason),
        ('task_lookup', 'not_checked', None),
    ])
    wanted['context']['requested_task_id'] = 'NOT-HERE'
    assert invoke(case, body) == (23, wanted)


@pytest.mark.parametrize('key', ['requirements_database', 'requirements_lock'])
def test_requirements_inside_target_refuse_without_allocating_anything(case, key):
    project, _, cfg, values = case
    path = str(project['app'] / 'private.sqlite')
    cfg['paths'][key] = path
    write_json(project['config_path'], cfg)
    values['requirements_db' if key == 'requirements_database' else 'requirements_lock'] = path
    reason = 'Requirements storage must be outside the served codebase'
    wanted = expected(case, status='rejected', ready=False, reason=reason, statuses=[
        ('configuration', 'rejected', reason), ('git', 'not_checked', None),
        ('commit_policy', 'not_checked', None), ('task_lookup', 'not_checked', None),
    ])
    assert invoke(case, request(case)) == (23, wanted)


def test_aliased_requirements_and_task_storage_are_rejected(case):
    project, _, cfg, values = case
    cfg['paths']['requirements_database'] = 'state.sqlite'
    write_json(project['config_path'], cfg)
    values['requirements_db'] = values['task_db']
    reason = 'Requirements DB/lock должны быть отделены от Task DB/lock'
    wanted = expected(case, status='rejected', ready=False, reason=reason, statuses=[
        ('configuration', 'rejected', reason), ('git', 'not_checked', None),
        ('commit_policy', 'not_checked', None), ('task_lookup', 'not_checked', None),
    ])
    assert invoke(case, request(case)) == (23, wanted)


def test_git_refusal_is_local_and_retains_reusable_owner_result(case, monkeypatch):
    from poise.infrastructure.projects import FileProjectSetup
    project, _, cfg, _ = case
    cfg['git']['base_ref'] = 'absent-preflight-branch'
    write_json(project['config_path'], cfg)
    calls = []
    original = FileProjectSetup._probe
    def observe(owner, configuration, enabled):
        calls.append((configuration['git']['repository'], enabled))
        return original(owner, configuration, enabled)
    monkeypatch.setattr(FileProjectSetup, '_probe', observe)
    git_calls = []
    original_run = subprocess.run
    def local_only(argv, *args, **kwargs):
        if argv[0] == 'git':
            assert argv[3] in ('rev-parse', 'check-ref-format'), argv
            git_calls.append(argv[3:])
        return original_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', local_only)
    code, actual = invoke(case, request(case))
    assert calls == [(str(project['app']), True)]
    assert git_calls == [
        ['rev-parse', '--show-toplevel'],
        ['rev-parse', '--verify', '--end-of-options', 'absent-preflight-branch^{commit}']]
    assert code == 23
    assert actual['status'] == 'rejected' and actual['ready'] is False
    assert actual['reason'].startswith('Local Git probe rev-parse failed: fatal:')
    assert actual['checks'] == [
        {'name': 'configuration', 'status': 'passed', 'reason': None},
        {'name': 'git', 'status': 'rejected', 'reason': actual['reason']},
        {'name': 'commit_policy', 'status': 'not_checked', 'reason': None},
        {'name': 'task_lookup', 'status': 'not_checked', 'reason': None}]
    assert actual['context']['base_ref'] == 'absent-preflight-branch'
    assert actual['recovery'] == [
        *fixture('recovery.json', case[3]), fixture('git-update-recovery.json', case[3])]


def test_unclassified_owner_error_is_inconclusive_not_invalid_or_ready(case, monkeypatch):
    from poise.infrastructure.projects import FileProjectSetup
    def unavailable(*args):
        raise RuntimeError('fixture Git observation unavailable')
    monkeypatch.setattr(FileProjectSetup, '_probe', unavailable)
    code, actual = invoke(case, request(case))
    assert code == 23 and actual['status'] == 'checked' and actual['ready'] is False
    assert actual['checks'][1]['status'] == 'unknown'
    assert 'fixture Git observation unavailable' in actual['checks'][1]['reason']
    assert actual['reason'] == actual['checks'][1]['reason']
    assert actual['checks'][2:] == [
        {'name': 'commit_policy', 'status': 'not_checked', 'reason': None},
        {'name': 'task_lookup', 'status': 'not_checked', 'reason': None}]
    assert actual['recovery'] == fixture('recovery.json', case[3])


@pytest.mark.parametrize('key,value', [
    ('requirements_database', None), ('requirements_lock', ''),
    ('requirements_database', 5),
])
def test_invalid_required_storage_parameter_exposes_no_fallback(case, key, value):
    project, _, cfg, _ = case
    cfg['paths'][key] = value
    write_json(project['config_path'], cfg)
    code, actual = invoke(case, request(case))
    assert code == 23
    assert actual['schema'] == 'project-preflight-result-1'
    assert actual['status'] == 'rejected' and actual['ready'] is False
    assert actual['reason'] == 'Requirements storage: explicit nonempty file path required'
    assert actual['context']['requirements_database' if key.endswith('database') else 'requirements_lock'] is None
    assert actual['checks'] == [
        {'name': 'configuration', 'status': 'rejected', 'reason': actual['reason']},
        {'name': 'git', 'status': 'not_checked', 'reason': None},
        {'name': 'commit_policy', 'status': 'not_checked', 'reason': None},
        {'name': 'task_lookup', 'status': 'not_checked', 'reason': None},
    ]
    assert actual['recovery'] == fixture('recovery.json', case[3])


def test_physically_aliased_storage_is_not_treated_as_separated_paths(case):
    project, _, _, values = case
    Path(values['task_db']).write_bytes(b'Existing Task storage bytes')
    Path(values['requirements_db']).unlink()
    os.link(values['task_db'], values['requirements_db'])
    code, actual = invoke(case, request(case))
    assert code == 23 and actual['ready'] is False
    assert actual['reason'] == 'Requirements storage физически совпадает с Task storage'
    assert actual['checks'][0]['status'] == 'rejected'
    assert actual['recovery'] == fixture('recovery.json', values)


@pytest.mark.parametrize('damage', ['missing-path', 'missing-manifest', 'malformed-manifest', 'project-mismatch'])
def test_configuration_failure_preserves_observed_identity_and_uncertainty(case, damage):
    project, _, cfg, values = case
    body = request(case)
    if damage == 'missing-path':
        del cfg['paths']['requirements_database']
        write_json(project['config_path'], cfg)
    elif damage == 'missing-manifest':
        project['config_path'].unlink()
    elif damage == 'malformed-manifest':
        project['config_path'].write_text('{invalid JSON')
    else:
        body['project_id'] = 'other-project'
    code, actual = invoke(case, body)
    assert code == 23 and actual['status'] == 'rejected' and actual['ready'] is False
    assert actual['checks'][0]['status'] == 'rejected'
    assert actual['checks'][0]['reason'] == actual['reason']
    assert actual['checks'][1:] == [
        {'name': name, 'status': 'not_checked', 'reason': None}
        for name in ('git', 'commit_policy', 'task_lookup')]
    assert actual['context']['installation_source'] == values['source']
    assert actual['context']['config_path'] == values['config']
    assert actual['context']['requested_project_id'] == body['project_id']
    if damage == 'project-mismatch':
        assert actual['context']['project_id'] == 'demo'
        assert 'other-project' in actual['reason'] and 'demo' in actual['reason']
    elif damage in ('missing-manifest', 'malformed-manifest'):
        assert actual['context']['project_id'] is None
        assert actual['context']['repository'] is None
    else:
        assert 'requirements_database' in actual['reason']
        assert actual['context']['requirements_database'] is None
    assert actual['recovery'] == fixture('recovery.json', values)


def test_check_does_not_construct_mutable_task_runtime_or_database(case, monkeypatch):
    from poise.runtime import Poise
    from poise.infrastructure.sqlite.database import Database
    def forbidden(*args, **kwargs):
        raise AssertionError('readonly check constructed a mutable owner')
    monkeypatch.setattr(Poise, '__init__', forbidden)
    monkeypatch.setattr(Database, '__init__', forbidden)
    assert invoke(case, request(case)) == (17, expected(case))


@pytest.mark.parametrize('change', ['schema', 'missing', 'bool', 'relative', 'extra'])
def test_invalid_request_rejects_before_manifest_git_or_store_read(case, change):
    project, _, _, _ = case
    body = request(case)
    if change == 'schema':
        body['schema'] = 'unsupported'
    elif change == 'missing':
        del body['project_id']
    elif change == 'bool':
        body['probe_repository'] = 1
    elif change == 'relative':
        body['config_path'] = 'project.json'
    else:
        body['fallback'] = True
    # No valid selected manifest is available: input validation must win first.
    project['config_path'].unlink()
    code, actual = invoke(case, body)
    assert code == 23
    assert set(actual) == {'schema', 'status', 'ready', 'reason', 'context', 'checks', 'recovery'}
    assert actual['schema'] == 'project-preflight-result-1'
    assert actual['status'] == 'rejected' and actual['ready'] is False
    assert isinstance(actual['reason'], str) and actual['reason']
    assert 'No such file' not in actual['reason']
    assert actual['checks'] == [
        {'name': name, 'status': 'not_checked', 'reason': None}
        for name in ('configuration', 'git', 'commit_policy', 'task_lookup')]
    assert actual['context']['repository'] is None
    assert actual['recovery'] == fixture('recovery.json', {
        **case[3], 'config': body.get('config_path') if change != 'relative' else None})


def test_cli_dispatch_uses_configured_success_code_and_exact_packet(case):
    project, settings, _, _ = case
    before = snapshot(project['root'].parent)
    run = subprocess.run([
        sys.executable, '-B', '-m', 'poise', 'project', 'check', '--settings', str(settings),
    ], input=json.dumps(request(case)), text=True, capture_output=True)
    assert snapshot(project['root'].parent) == before
    assert run.returncode == 17, run.stderr + run.stdout
    assert run.stderr == ''
    assert json.loads(run.stdout) == expected(case)


def test_unavailable_settings_retains_existing_two_key_envelope(case):
    project, _, _, _ = case
    path = project['root'] / 'bad-settings.json'
    write_json(path, {})
    before = snapshot(project['root'].parent)
    output = io.StringIO()
    code = execute(path, io.BytesIO(b'{}'), output, action='check')
    assert snapshot(project['root'].parent) == before
    assert code == 2
    packet = json.loads(output.getvalue())
    assert set(packet) == {'status', 'reason'}
    assert packet['status'] == 'rejected'
    assert 'project setup settings' in packet['reason']


def test_missing_settings_argument_remains_argparse_transport_error(case):
    project = case[0]
    before = snapshot(project['root'].parent)
    run = subprocess.run([sys.executable, '-B', '-m', 'poise', 'project', 'check'],
                         input='{}', text=True, capture_output=True)
    assert snapshot(project['root'].parent) == before
    assert run.returncode == 2 and run.stdout == ''
    assert '--settings' in run.stderr
