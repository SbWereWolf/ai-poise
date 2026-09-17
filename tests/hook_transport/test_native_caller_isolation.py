"""Public native inputs, never the copied launcher, identify a Task caller."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from batch.helpers import request
from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from poise.infrastructure.session_establishment import direct_caller
from .helpers import settings, install, event


def fixture_service(project, tmp_path):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native = event(session='sender')
    native['cwd'] = str(project['root'])
    service.event(installed['definition_path'], native)
    return service, installed


def bootstrap(task=None):
    return request('bootstrap', {'task': task, 'decision': None,
                                 'feedback': None, 'rework_stage': None})


def snapshot(project, service):
    paths = (project['root'] / project['cfg']['paths']['state'] /
             project['cfg']['paths']['database'], service.settings.database)
    result = []
    for path in paths:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            result.append({name: sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
                           for name, in tables.fetchall()})
    return result


def test_conflicting_native_variables_reject_before_creating_binding(tmp_path):
    path = tmp_path / 'caller.json'
    with pytest.raises(PoiseError, match='native.*identity|native.*session'):
        direct_caller('P', {'CODEX_SESSION_ID': 'sender', 'CODEX_THREAD_ID': 'delegate',
                            'POISE_CALLER_BINDING': str(path)})
    assert not path.exists()


def test_inherited_generated_binding_cannot_alias_distinct_native_threads(tmp_path):
    path = tmp_path / 'inherited.json'
    # Native work must not silently prefer this shared fallback file.
    with pytest.raises(PoiseError, match='POISE_CALLER_BINDING'):
        direct_caller('P', {'CODEX_THREAD_ID': 'delegate', 'POISE_CALLER_BINDING': str(path)})
    assert not path.exists()


@pytest.mark.parametrize('native_env', [
    {}, {'CODEX_THREAD_ID': 'delegate'},
    {'CODEX_SESSION_ID': 'sender', 'CODEX_THREAD_ID': 'delegate'},
])
def test_copied_sender_launcher_rejected_before_task_mutation(project, tmp_path, monkeypatch, native_env):
    service, _ = fixture_service(project, tmp_path)
    binding = service.latest_binding('sender', 'primary')
    for key in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING'):
        monkeypatch.delenv(key, raising=False)
    for key, value in native_env.items():
        monkeypatch.setenv(key, value)
    before = snapshot(project, service)
    with pytest.raises(PoiseError, match='native.*identity|native.*session'):
        service.work(binding['binding_path'], bootstrap(deepcopy(project['task'])))
    assert snapshot(project, service) == before
    assert not hasattr(service, 'runtime')


@pytest.mark.parametrize('agent', ['delegate', '', 17])
def test_child_or_invalid_actor_hook_never_rebinds_parent(project, tmp_path, agent):
    service, installed = fixture_service(project, tmp_path)
    before = snapshot(project, service)
    native = event('Stop', session='sender')
    native.update(cwd=str(project['root']), agent_id=agent)
    with pytest.raises(PoiseError, match='native.*session|native.*agent'):
        service.event(installed['definition_path'], native)
    assert snapshot(project, service) == before


def test_own_launcher_works_without_identity_override(project, tmp_path):
    service, _ = fixture_service(project, tmp_path)
    binding = service.latest_binding('sender', 'primary')
    env = {k: v for k, v in os.environ.items() if k not in
           ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING')}
    env['CODEX_THREAD_ID'] = 'sender'  # observed fixture host, not launcher substitution
    completed = subprocess.run([binding['launcher']], input=json.dumps(bootstrap()),
                               env=env, text=True, capture_output=True, timeout=12)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['session'] == 'sender'
    text = Path(binding['launcher']).read_text()
    assert 'CODEX_THREAD_ID=' not in text and 'CODEX_SESSION_ID=' not in text


@pytest.mark.parametrize('value', ['', ' ', '.', '..', 'bad/session', 'bad\\session', 'bad\0session'])
def test_invalid_direct_native_identity_is_rejected_without_fallback_write(tmp_path, value):
    target = tmp_path / 'unused.json'
    with pytest.raises(PoiseError):
        direct_caller('P', {'CODEX_THREAD_ID': value, 'POISE_CALLER_BINDING': str(target)})
    assert not target.exists()


def test_matching_native_variables_keep_the_observed_identity():
    caller = direct_caller('P', {'CODEX_THREAD_ID': 'same', 'CODEX_SESSION_ID': 'same'})
    assert caller.native_session == 'same'


def launch(binding, packet, session):
    from .helpers import host_environment
    process = subprocess.run([binding['launcher']], input=json.dumps(packet),
                             env=host_environment(session), text=True, capture_output=True, timeout=20)
    value = json.loads(process.stdout) if process.stdout else None
    if isinstance(value, dict) and 'response_path' in value:
        value = json.loads(Path(value['response_path']).read_text())
    return process, value


def handoff(identity):
    return request('handoff', {'request_id': identity, 'reason': 'explicit separate-session transfer',
                              'result': None, 'commit_message': 'Preserve owner WIP', 'artifact_paths': []})


def claims(project):
    path = (project['root'] / project['cfg']['paths']['state'] /
            project['cfg']['paths']['database'])
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        return (dict(db.execute('SELECT id,claimed_by FROM tasks')),
                dict(db.execute('SELECT id,task_id FROM sessions')))


@pytest.mark.parametrize('foreign_operation', ['bootstrap', 'handoff'])
def test_concurrent_copied_launcher_cannot_replace_or_release_sender(project, tmp_path, foreign_operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    service, installed = fixture_service(project, tmp_path)
    child = event(session='delegate'); child['cwd'] = str(project['root'])
    service.event(installed['definition_path'], child)
    sender = service.latest_binding('sender', 'primary')
    delegate = service.latest_binding('delegate', 'primary')
    run, context = launch(sender, bootstrap(deepcopy(project['task'])), 'sender')
    assert run.returncode == 0, run.stderr
    wip = Path(context['worktree']) / 'owner-wip.txt'; wip.write_text('must survive')
    before = claims(project)
    other = deepcopy(project['task']); other['id'] = 'T2'
    packet = bootstrap(other) if foreign_operation == 'bootstrap' else handoff('foreign-release')
    ready = Barrier(2)
    def concurrent(binding, body, session):
        ready.wait(timeout=10)
        return launch(binding, body, session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        own = pool.submit(concurrent, sender, bootstrap(), 'sender')
        copied = pool.submit(concurrent, sender, packet, 'delegate')
        (ok, current), (denied, _) = own.result(), copied.result()
    assert ok.returncode == 0 and current['task'] == context['task'], ok.stderr
    assert denied.returncode == 2 and 'native session identity' in (denied.stderr + denied.stdout)
    assert claims(project) == before
    assert wip.read_text() == 'must survive'
    # The delegate's own genuine launcher is still usable and cannot acquire a live owner Task.
    blocked, _ = launch(delegate, bootstrap({'id': context['task']}), 'delegate')
    assert blocked.returncode == 2 and 'live' in (blocked.stderr + blocked.stdout)
    assert claims(project) == before
    saved, report = launch(sender, handoff('owner-release'), 'sender')
    assert saved.returncode == 0 and report['status'] == 'handed_off', saved.stderr
    resumed, next_context = launch(delegate, bootstrap({'id': context['task']}), 'delegate')
    assert resumed.returncode == 0, resumed.stderr
    assert next_context['worktree'] == context['worktree'] and wip.read_text() == 'must survive'
    owners, sessions = claims(project)
    assert owners[context['task']] == 'delegate' and sessions['sender'] is None
    assert sessions['delegate'] == context['task']
