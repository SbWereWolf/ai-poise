"""SU1-SU7: real source/native/package boundaries, GREEN at implementation."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys

import pytest

from batch.helpers import request
from conftest import git, write_json
from hook_transport.helpers import host_environment
from hook_transport.preparation_package_support import installed_probe, output_directory
from hook_transport.preparation_source_upgrade_support import (
    FIXTURES, FORMS, ROOT, SESSION, arrange_hooks, assert_current, assert_probe,
    assert_captures_preserved, assert_reconfiguration_reply, assert_reconfigured_state, assert_target_summary,
    assert_session_preserved, clean_environment, copy_candidate, fixture_script, lifecycle,
    operation_result, public_reconfigure, read_json, ready_handler_result, reconfigure_packet, resume, snapshot, write_launcher,
)
from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from hook_transport.helpers import event
from runtime_services.worktree_preparation_support import (
    FIXTURES as PREPARATION_FIXTURES, arrange, capture_origin, use_environment_handler,
)


@pytest.fixture(scope='module')
def source_copies(tmp_path_factory):
    return {'plain': ROOT,
            'unicode': copy_candidate(tmp_path_factory.mktemp('source-copy') / 'код Ω с пробелами')}


@pytest.mark.parametrize('path_kind', ['plain', 'unicode'])
@pytest.mark.parametrize('form', FORMS)
def test_old_owned_launcher_upgrades_on_real_resume(project, tmp_path, source_copies, path_kind, form):
    case = arrange_hooks(project, tmp_path, source_copies[path_kind])
    write_launcher(case, form)
    before = snapshot(case)
    result = resume(case)
    assert result['hookSpecificOutput']['hookEventName'] == 'SessionStart'
    assert_current(case)
    after = snapshot(case)
    assert_session_preserved(before, after)
    added = lifecycle(after['rows']['events'])[len(lifecycle(before['rows']['events'])):]
    assert len(added) == 1 and added[0][2:4] == (SESSION, 'SessionStart')
    assert json.loads(added[0][4]) == {'turn_id': None, 'source': 'resume', 'workspace': str(project['app'])}


@pytest.mark.parametrize('path_kind', ['plain', 'unicode'])
@pytest.mark.parametrize('form', FORMS)
def test_old_owned_launcher_upgrades_on_public_samefile_reconfigure(project, tmp_path, source_copies, path_kind, form):
    case = arrange_hooks(project, tmp_path, source_copies[path_kind])
    write_launcher(case, form)
    packet = reconfigure_packet(case)
    before = snapshot(case)
    code, response = public_reconfigure(case, packet)
    assert code == 0, response
    first_captures = assert_reconfiguration_reply(case, packet, response)
    assert_current(case)
    after = snapshot(case)
    assert_reconfigured_state(case, packet, before, after)
    repeated_code, repeated = public_reconfigure(case, packet)
    assert repeated_code == 0
    assert_reconfiguration_reply(case, packet, repeated)
    assert operation_result(repeated) == operation_result(response)
    assert snapshot(case) == after
    assert_captures_preserved(first_captures)
    result = resume(case, response['definition_path'])
    assert_target_summary(case, packet, result)
    assert_session_preserved(after, snapshot(case))


@pytest.mark.parametrize('route', ['resume', 'reconfigure'])
@pytest.mark.parametrize('kind', ['foreign', 'near-match'])
def test_foreign_launcher_is_preserved_before_any_upgrade(project, tmp_path, route, kind):
    case = arrange_hooks(project, tmp_path)
    launcher = Path(case['binding']['launcher'])
    original = fixture_script('H5', case['coordinates'])
    launcher.write_bytes(b'#!/bin/sh\nexit 23\n' if kind == 'foreign' else
                         original.replace(b' "$@"', b' --foreign "$@"'))
    packet = reconfigure_packet(case)
    before = snapshot(case)
    if route == 'resume':
        with pytest.raises(PoiseError, match='Launcher was changed externally'):
            resume(case)
    else:
        code, reply = public_reconfigure(case, packet)
        assert code == 2 and reply['status'] == 'rejected'
        assert 'Foreign launcher content before reconfiguration' in reply['reason']
    assert snapshot(case) == before


@pytest.mark.parametrize('route', ['resume', 'reconfigure'])
def test_current_literal_launcher_keeps_its_binding(project, tmp_path, route):
    case = arrange_hooks(project, tmp_path)
    write_launcher(case, 'current')
    before = snapshot(case)
    if route == 'resume':
        resume(case)
        assert_session_preserved(before, snapshot(case))
    else:
        packet = reconfigure_packet(case)
        code, reply = public_reconfigure(case, packet)
        assert code == 0
        first_captures = assert_reconfiguration_reply(case, packet, reply)
        after = snapshot(case)
        assert_reconfigured_state(case, packet, before, after)
        repeated_code, repeated = public_reconfigure(case, packet)
        assert repeated_code == 0
        assert_reconfiguration_reply(case, packet, repeated)
        assert operation_result(repeated) == operation_result(reply)
        assert snapshot(case) == after
        assert_captures_preserved(first_captures)
    assert_current(case)


def prepare_ended_release(case):
    """Create another real binding and commit the selected end before the baseline."""
    native = event(session='unselected-source-session')
    native['cwd'] = str(case['project']['app'])
    case['service'].event(case['installed']['definition_path'], native)
    return case['service'].registry.record_event(SESSION, 'SessionEnd', 'target-issued-end', None)


@pytest.mark.parametrize('path_kind', ['plain', 'unicode'])
@pytest.mark.parametrize('consumer', ['native', 'reconfigure', 'release'])
def test_target_guarded_issuer_is_consumed_without_losing_owned_state(
        project, tmp_path, source_copies, path_kind, consumer):
    # Earliest GREEN: the separate integration compatibility implementation.
    case = arrange_hooks(project, tmp_path, source_copies[path_kind], target_issuer=True)
    service = HookService(case['settings_path'])
    if consumer == 'release':
        end = prepare_ended_release(case)
    before = snapshot(case)
    if consumer == 'native':
        result = resume(case)
        assert result['hookSpecificOutput']['hookEventName'] == 'SessionStart'
        assert_current(case)
        after = snapshot(case)
        assert_session_preserved(before, after)
        added = after['rows']['events'][len(before['rows']['events']):]
        assert len(added) == 1 and added[0][2:4] == (SESSION, 'SessionStart')
        assert json.loads(added[0][4]) == {
            'turn_id': None, 'source': 'resume', 'workspace': str(project['app'])}
        for field in ('installations', 'operations'):
            assert after['rows'][field] == before['rows'][field]
        hooks = str(service.settings.hooks_file)
        assert after['files'][hooks] == before['files'][hooks]
        # Diagnosis must recognize actual old publisher groups, without modifying them.
        diagnosed = service.diagnose({'definition_path': case['installed']['definition_path'],
            'session_id': SESSION, 'event': 'SessionStart', 'event_id': None,
            'run_ids': [], 'probe_cwd': None})
        assert diagnosed['installation']['status'] == 'configured'
        assert diagnosed['binding']['status'] == 'valid'
        assert diagnosed['native_event']['status'] == 'observed'
        assert diagnosed['native_event']['id'] == added[0][0]
        assert diagnosed['native_event']['source'] == 'resume'
        assert snapshot(case) == after
    elif consumer == 'reconfigure':
        packet = reconfigure_packet(case)
        code, reply = public_reconfigure(case, packet)
        assert code == 0, reply
        captures = assert_reconfiguration_reply(case, packet, reply)
        assert_current(case)
        after = snapshot(case)
        assert_reconfigured_state(case, packet, before, after)
        again_code, again = public_reconfigure(case, packet)
        assert again_code == 0, again
        assert_reconfiguration_reply(case, packet, again)
        assert operation_result(again) == operation_result(reply)
        assert snapshot(case) == after
        assert_captures_preserved(captures)
        result = resume(case, reply['definition_path'])
        assert_target_summary(case, packet, result)
        assert_session_preserved(after, snapshot(case))
        assert_captures_preserved(captures)
    else:
        root = Path(case['binding']['binding_path']).parent
        removed = [case['binding']['binding_path'], case['binding']['launcher'],
                   str(root / case['settings']['capability_summary']['file'])]
        result = service.release_ended_binding(case['binding'], end)
        assert result == {'status': 'released', 'session_id': SESSION, 'removed_files': removed}
        assert not root.exists()
        assert service.registry.liveness(SESSION).value == 'dead'
        after = snapshot(case)
        assert after['rows']['bindings'] == [row for row in before['rows']['bindings'] if row[0] != SESSION]
        for field in ('installations', 'operations', 'events'):
            assert after['rows'][field] == before['rows'][field]
        for field in ('files', 'modes'):
            assert after[field] == {key: value for key, value in before[field].items() if key not in removed}
        assert service.release_ended_binding(case['binding'], end) == {
            'status': 'already_released', 'session_id': SESSION}
        assert snapshot(case) == after


@pytest.mark.parametrize('path_kind', ['plain', 'unicode'])
@pytest.mark.parametrize('consumer', ['native', 'reconfigure', 'release'])
@pytest.mark.parametrize('mutation', ['foreign', 'guard-body', 'extra-argv'])
def test_target_guarded_near_match_keeps_all_owned_material(
        project, tmp_path, source_copies, path_kind, consumer, mutation):
    # Earliest GREEN: test construction, before the separate product correction.
    case = arrange_hooks(project, tmp_path, source_copies[path_kind], target_issuer=True)
    if consumer == 'release':
        end = prepare_ended_release(case)
    launcher = Path(case['binding']['launcher'])
    original = launcher.read_bytes()
    changed = {
        'foreign': b'#!/bin/sh\nexit 23\n',
        'guard-body': original.replace(b'valid=0', b'valid=1'),
        'extra-argv': original.removesuffix(b' "$@"\n') + b' --foreign "$@"\n',
    }[mutation]
    assert changed != original
    if mutation == 'extra-argv':
        assert original.endswith(b' "$@"\n')
        assert changed.endswith(b' --foreign "$@"\n')
        assert changed.removesuffix(b' --foreign "$@"\n') == original.removesuffix(b' "$@"\n')
        original_argv = shlex.split(original.decode().splitlines()[1])
        changed_argv = shlex.split(changed.decode().splitlines()[1])
        assert changed_argv[3] == original_argv[3]
        assert changed_argv == [*original_argv[:-1], '--foreign', original_argv[-1]]
    launcher.write_bytes(changed)
    before = snapshot(case)
    if consumer == 'native':
        with pytest.raises(PoiseError, match='Launcher was changed externally'):
            resume(case)
    elif consumer == 'reconfigure':
        code, reply = public_reconfigure(case, reconfigure_packet(case))
        assert code == 2 and reply['status'] == 'rejected'
        assert 'Foreign launcher content before reconfiguration' in reply['reason']
    else:
        with pytest.raises(PoiseError, match='Foreign launcher content before release'):
            HookService(case['settings_path']).release_ended_binding(case['binding'], end)
    assert snapshot(case) == before


def test_real_stride_loads_selected_handler_outside_source_cwd(project, tmp_path, source_copies):
    # Keep the real fixture registry/setup; do not replace it with setup_case.
    setup_path = Path(project['cfg']['runtime_resources']['project_setup'])
    setup = read_json(setup_path)
    tools_root = (setup_path.parent / setup['root']).resolve()
    code_root = source_copies['unicode']
    launch = read_json(FIXTURES / 'launch-settings.json')
    launch.update(poise_root=str(tools_root), data_root=str(tmp_path), project_setup=str(setup_path),
                  command=[sys.executable, '-P', '-s', '-B', str(FIXTURES / 'import_probe.py')])
    settings = write_json(tmp_path / 'launch settings.json', launch)
    environment_file = tmp_path / 'launcher.env'
    environment_file.write_text('STRIDE_PYTHON=' + shlex.quote(sys.executable) + '\n'
                                + 'STRIDE_CONFIG=' + shlex.quote(str(settings)) + '\n')
    cwd = tmp_path / 'external cwd'
    cwd.mkdir()
    process = subprocess.run(['bash', str(ROOT / 'stride.sh'), '--poise-root', str(code_root),
                              '--project', project['cfg']['project'], str(environment_file), 'work'],
                             cwd=cwd, env=clean_environment(), capture_output=True)
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert process.stderr == b''
    observed = json.loads(process.stdout)
    assert_probe(observed, code_root, str(project['config_path']))
    assert observed['poise_origin'] == str(code_root / 'src/poise/__init__.py')
    assert observed['handler_origin'] == str(code_root / 'apps/bootstrap/environment_result.py')
    assert observed['python_path'] == os.pathsep.join((str(code_root / 'src'), str(code_root)))


def test_real_native_launcher_reaches_builtin_preparation(project, tmp_path, monkeypatch):
    case = arrange(project, monkeypatch)
    use_environment_handler(case)
    hook = arrange_hooks(project, tmp_path)
    before = capture_origin(case)
    body = read_json(PREPARATION_FIXTURES / 'bootstrap-input.json')
    body['task'] = deepcopy(project['task'])
    cwd = tmp_path / 'unrelated native cwd'
    cwd.mkdir()
    environment = {key: value for key, value in host_environment(SESSION).items()
                   if key not in ('PYTHONPATH', 'PYTHONHOME')}
    process = subprocess.run(['bash', hook['binding']['launcher']], input=json.dumps(request('bootstrap', body)).encode(),
                             cwd=cwd, env=environment, capture_output=True)
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert process.stderr == b''
    response_path = Path(json.loads(process.stdout)['response_path'])
    response = read_json(response_path)
    output = output_directory(tmp_path)
    shutil.copyfile(response_path, output / 'native-work-response.json')
    assert response.get('preparation', {}).get('status') == 'ready', 'P004_NATIVE_PREPARATION_NOT_READY'
    assert response['worktree'] == before['worktree']
    root = Path(response['worktree'])
    assert root.is_dir() and git(root, 'rev-parse', 'HEAD') == before['base']
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'tasks/T1'
    events = [json.loads(line) for line in case['events'].read_text().splitlines()]
    assert [item['operation'] for item in events] == ['infra', 'deps', 'check']
    assert all(item['git_root'] == item['workspace_argument'] == item['cwd'] == str(root) for item in events)
    assert all(item['task'] == 'T1' and item['project'] == 'demo' and item['branch'] == 'tasks/T1'
               and item['head'] == before['base'] and item['input'] == 'selected input Ω\n' for item in events)
    preparation = response['preparation']
    report = read_json(preparation['receipt_path'])
    assert hashlib.sha256(Path(preparation['receipt_path']).read_bytes()).hexdigest() == preparation['receipt_sha256']
    for name in ('task_id', 'project_id', 'worktree', 'branch', 'base'):
        assert report[name] == before[name]
    assert report['configuration']['path'] == before['configuration_path']
    assert Path(report['configuration']['snapshot_path']).read_bytes() == before['configuration_bytes']
    with sqlite3.connect(case['runtime'].store.path) as connection:
        registered = dict(connection.execute('SELECT a.path,a.digest FROM artifacts a JOIN task_artifacts t '
                                             'ON a.id=t.artifact_id WHERE t.task_id=?', ('T1',)))
    assert registered[preparation['receipt_path']] == preparation['receipt_sha256']
    for operation in report['operations']:
        assert operation['result'] == ready_handler_result(
            (PREPARATION_FIXTURES / 'ready.stdout').read_text(), 'full diagnostic π\n' * 256)
        command, = operation['commands']
        for stream, expected in [('stdout', (PREPARATION_FIXTURES / 'ready.stdout').read_bytes()),
                                 ('stderr', ('full diagnostic π\n' * 256).encode())]:
            path = command[stream + '_path']
            assert Path(path).read_bytes() == expected
            assert registered[path] == hashlib.sha256(expected).hexdigest() == command[stream + '_sha256']


def test_installed_wheel_loads_handler_without_source_path(tmp_path):
    observed, environment = installed_probe(tmp_path)
    assert_probe(observed, environment, None)
    assert observed['python_path'] is None
    assert Path(observed['poise_origin']).parts[-2:] == ('poise', '__init__.py')
    assert Path(observed['handler_origin']).parts[-3:] == ('apps', 'bootstrap', 'environment_result.py')
