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
