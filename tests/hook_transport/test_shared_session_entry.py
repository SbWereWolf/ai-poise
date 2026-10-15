"""Shared native configuration owns discovery; Git owns worktree membership."""
from copy import deepcopy
import json
import io
import os
import shlex
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from conftest import git, write_json
from poise.common import PoiseError, digest, file_digest
from poise.infrastructure.hook_transport import HookService
from poise.infrastructure.locking import exclusive_lock
from poise.interfaces.hook_transport import execute
from hook_transport.helpers import definition, event, install, settings
from batch.helpers import request


def prepared(project, tmp_path):
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    raw['cwd_roots'] = [str(project['app'])]
    write_json(path, raw)
    service = HookService(path)
    fixtures = Path(__file__).resolve().parents[2] / 'fixtures/hook-transport'
    source_foreign = json.loads((fixtures / 'session-source-foreign.json').read_text())
    write_json(service.settings.hooks_file, source_foreign)
    installed = install(service, revision=service.revision())
    native = event(session='preserved-parent')
    native['cwd'] = str(project['app'])
    service.event(installed['definition_path'], native)
    prompt = event('UserPromptSubmit', session='preserved-parent')
    prompt['cwd'] = str(project['app'])
    service.event(installed['definition_path'], prompt)
    target = tmp_path / 'user configuration' / '.codex' / 'hooks.json'
    target_foreign = json.loads((fixtures / 'session-target-foreign.json').read_text())
    write_json(target, target_foreign)
    updated = deepcopy(raw)
    updated['hooks_file'] = str(target)
    updated['repository_scope'] = {'repository': str(project['app']), 'timeout_seconds': 3.0}
    updated['hooks_lock'] = str(tmp_path / 'user configuration' / 'hooks.lock')
    selected = definition()
    selected['events'][0]['matcher'] = ''
    packet = {'request_id': 'share-native-entry',
              'expected_settings_digest': digest(raw),
              'expected_revision': file_digest(service.settings.hooks_file),
              'target_revision': file_digest(target),
              'settings': updated, 'definition': selected}
    return service, packet, target, {'source': source_foreign, 'target': target_foreign}


def hook_state(service):
    with sqlite3.connect(service.settings.database) as connection:
        return {'bindings': connection.execute('SELECT id,data,latest_message FROM bindings ORDER BY id').fetchall(),
                'events': connection.execute('SELECT id,at,binding,event,data FROM hook_events ORDER BY id').fetchall()}


def assert_shared_state(service, packet, target, foreign, receipt):
    updated = HookService(service.settings.path)
    assert json.loads(service.settings.hooks_file.read_text()) == foreign['source']
    document = json.loads(target.read_text())
    assert document['description'] == foreign['target']['description']
    assert document['hooks']['PostToolUse'] == foreign['target']['hooks']['PostToolUse']
    assert foreign['target']['hooks']['SessionStart'][0] in document['hooks']['SessionStart']
    for kind in ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd']:
        owned = [group for group in document['hooks'][kind]
                 if any(str(service.settings.path) in hook.get('command', '')
                        for hook in group['hooks'])]
        assert len(owned) == 1
    binding = updated.latest_binding('preserved-parent', 'primary')
    assert json.loads(Path(binding['binding_path']).read_text()) == binding
    assert binding['settings_digest'] == digest(packet['settings'])
    assert binding['definition_path'] == receipt['definition_path']
    return updated


def cli(service, definition_path, native, cwd):
    document = json.loads(service.settings.hooks_file.read_text())
    commands = [handler['command'] for group in document['hooks'][native['hook_event_name']]
                for handler in group['hooks']
                if str(service.settings.path) in handler.get('command', '')]
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert definition_path in argv
    result = subprocess.run(
        argv,
        input=json.dumps(native), text=True, capture_output=True, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_reconfigure_moves_owned_groups_and_preserves_existing_session(project, tmp_path):
    service, packet, target, foreign = prepared(project, tmp_path)
    old_binding = service.latest_binding('preserved-parent', 'primary')
    old_launcher = Path(old_binding['launcher']).read_bytes()
    before = hook_state(service)
    source = service.settings.hooks_file
    receipt = service.reconfigure(packet)

    updated = HookService(service.settings.path)
    binding = updated.latest_binding('preserved-parent', 'primary')
    assert {k: v for k, v in binding.items() if k not in ('settings_digest', 'definition_path')} == {
        k: v for k, v in old_binding.items() if k not in ('settings_digest', 'definition_path')}
    assert binding['settings_digest'] == digest(packet['settings'])
    assert binding['definition_path'] == receipt['definition_path']
    assert Path(binding['launcher']).read_bytes() == old_launcher
    assert_shared_state(service, packet, target, foreign, receipt)
    after = hook_state(updated)
    assert after['events'][:len(before['events'])] == before['events']
    assert after['bindings'][0][2] == before['bindings'][0][2]
    assert before['bindings'][0][2] is not None
    environment = dict(os.environ, CODEX_THREAD_ID='preserved-parent')
    environment.pop('CODEX_SESSION_ID', None)
    completed = subprocess.run(['bash', old_binding['launcher']],
        input=json.dumps(request('show', {'queries': [{'id': 'messages', 'kind': 'messages'}]})),
        capture_output=True, text=True, env=environment, cwd=project['app'])
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['status'] == 'read_only'
    assert updated.reconfigure(packet) == receipt


def test_public_reconfiguration_packet_publishes_the_shared_entry(project, tmp_path):
    service, packet, target, foreign = prepared(project, tmp_path)
    completed = subprocess.run(shlex.split(service.settings.command('runtime-config')),
        input=json.dumps({'operation': 'reconfigure', 'input': packet}), capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    preview = json.loads(completed.stdout)
    response = json.loads(Path(preview['response_path']).read_text())
    assert response['status'] == 'installed'
    assert response['capability_checks']['ready'] is True
    assert_shared_state(service, packet, target, foreign, response)


def test_reconfiguration_refuses_unregistered_managed_target_group(project, tmp_path):
    service, packet, target, _ = prepared(project, tmp_path)
    document = json.loads(target.read_text())
    source = json.loads(service.settings.hooks_file.read_text())
    owned = next(group for group in source['hooks']['SessionStart']
                 if any(str(service.settings.path) in hook.get('command', '') for hook in group['hooks']))
    changed = deepcopy(owned)
    changed['hooks'][0]['statusMessage'] = 'Unregistered operator change'
    document['hooks']['SessionStart'].append(changed)
    write_json(target, document)
    packet['target_revision'] = file_digest(target)
    before = target.read_bytes()
    binding_before = hook_state(service)

    with pytest.raises(PoiseError, match='Unregistered managed hook groups'):
        service.reconfigure(packet)

    assert target.read_bytes() == before
    assert hook_state(service) == binding_before


@pytest.mark.parametrize('source', ['startup', 'resume', 'clear', 'compact', 'fork'])
def test_shared_entry_binds_registered_worktree_without_local_config(project, tmp_path, source):
    service, packet, target, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    worktree = tmp_path / 'separate working tree'
    git(project['app'], 'worktree', 'add', '--detach', str(worktree), 'HEAD')
    assert not (worktree / '.codex/hooks.json').exists()
    native = event(session='worktree-' + source)
    native.update(source=source, cwd=str(worktree))

    result = cli(updated, receipt['definition_path'], native, worktree)

    binding = updated.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in result['hookSpecificOutput']['additionalContext']
    assert binding['external_session'] == native['session_id']


@pytest.mark.parametrize('kind', ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'])
@pytest.mark.parametrize('git_repository', [True, False])
def test_shared_entry_ignores_unrelated_repository_before_runtime(project, tmp_path, kind, git_repository):
    service, packet, target, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    if git_repository:
        git(unrelated, 'init')
    native = event(kind, session='outside-project')
    native['cwd'] = str(unrelated)
    before = hook_state(updated)
    workflow = next(iter(project['cfg']['workflows'].values()))
    (project['config_path'].parent / workflow).unlink()

    assert cli(updated, receipt['definition_path'], native, unrelated) == {}
    assert hook_state(updated) == before


@pytest.mark.parametrize('failure', [OSError('Git unavailable'), subprocess.TimeoutExpired('git', 3.0)])
def test_shared_entry_reports_git_failure_without_creating_binding(project, tmp_path, monkeypatch, failure):
    service, packet, _, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    before = hook_state(updated)
    native = event(session='failed-git-probe')
    native['cwd'] = str(project['app'])

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr('poise.infrastructure.git_transport.subprocess.run', fail)
    output, error = io.StringIO(), io.StringIO()
    code = execute('hook', SimpleNamespace(settings=updated.settings.path,
        definition=Path(receipt['definition_path'])), io.BytesIO(json.dumps(native).encode()), output, error)
    assert code == 1
    assert output.getvalue() == ''
    assert 'did not complete' in error.getvalue()
    assert hook_state(updated) == before


def test_empty_git_success_is_not_repository_membership(project, tmp_path, monkeypatch):
    service, packet, _, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    before = hook_state(updated)
    native = event(session='empty-git-output')
    native['cwd'] = str(tmp_path)

    def empty(*args, **kwargs):
        return {'argv': ['git'], 'actual_exit_code': 0, 'stdout': '', 'stderr': ''}

    monkeypatch.setattr('poise.infrastructure.git_transport.run_git_receipt', empty)
    output, error = io.StringIO(), io.StringIO()
    code = execute('hook', SimpleNamespace(settings=updated.settings.path,
        definition=Path(receipt['definition_path'])), io.BytesIO(json.dumps(native).encode()), output, error)
    assert code == 1
    assert 'absolute Git common directory' in error.getvalue()
    assert output.getvalue() == ''
    assert hook_state(updated) == before


@pytest.mark.parametrize('changed', ['source', 'target', 'settings'])
def test_reconfiguration_refuses_observed_drift_without_overwriting(project, tmp_path, changed):
    service, packet, target, _ = prepared(project, tmp_path)
    path = {'source': service.settings.hooks_file, 'target': target,
            'settings': service.settings.path}[changed]
    raw = json.loads(path.read_text())
    raw['operator_note'] = 'A newer operator change must survive.'
    write_json(path, raw)
    paths = [service.settings.path, service.settings.hooks_file, target]
    before = {str(p): p.read_bytes() for p in paths}

    with pytest.raises(PoiseError):
        service.reconfigure(packet)

    assert {str(p): p.read_bytes() for p in paths} == before


def test_reconfiguration_keeps_task_history_and_claims(project, tmp_path, monkeypatch):
    service, packet, _, _ = prepared(project, tmp_path)
    binding = service.latest_binding('preserved-parent', 'primary')
    monkeypatch.setenv('CODEX_THREAD_ID', 'preserved-parent')
    monkeypatch.delenv('CODEX_SESSION_ID', raising=False)
    service.work(binding['binding_path'], request('bootstrap', {
        'task': project['task'], 'decision': None, 'feedback': None, 'rework_stage': None}))
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
            'FROM tasks ORDER BY id').fetchall()
        history = connection.execute('SELECT seq,at,session_id,task_id,event,data FROM journal ORDER BY seq').fetchall()
        events = connection.execute('SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq').fetchall()
    assert len(before) == 1
    assert (before[0][0], before[0][1], before[0][4]) == ('T1', 'active', 'preserved-parent')
    assert history
    assert events
    service.reconfigure(packet)
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
            'FROM tasks ORDER BY id').fetchall()
        assert connection.execute('SELECT seq,at,session_id,task_id,event,data FROM journal ORDER BY seq').fetchall() == history
        assert connection.execute('SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq').fetchall() == events
    assert after == before


@pytest.mark.parametrize('point', ['source', 'binding', 'settings'])
def test_shared_publication_retries_after_partial_native_file_write(project, tmp_path, monkeypatch, point):
    service, packet, target, foreign = prepared(project, tmp_path)
    import poise.infrastructure.hook_transport as owner
    original = owner.atomic_write
    failed = False
    binding = service.latest_binding('preserved-parent', 'primary')
    fault_path = {'source': service.settings.hooks_file, 'settings': service.settings.path,
                  'binding': Path(binding['binding_path'])}[point]

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if Path(path) == fault_path and not failed:
            failed = True
            raise OSError('native source publication interrupted')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(owner, 'atomic_write', fail_once)
    with pytest.raises(OSError, match='publication interrupted'):
        service.reconfigure(packet)
    assert failed
    assert len(json.loads(target.read_text())['hooks']['SessionStart']) == 2
    with sqlite3.connect(service.settings.database) as connection:
        pending = json.loads(connection.execute('SELECT data FROM operations WHERE id=?',
            (packet['request_id'],)).fetchone()[0])
    assert pending['phase'] == 'prepared'
    receipt = HookService(service.settings.path).reconfigure(packet)
    assert receipt['hooks_path'] == str(target)
    assert_shared_state(service, packet, target, foreign, receipt)


def test_pending_retry_preserves_newer_foreign_change(project, tmp_path, monkeypatch):
    service, packet, target, _ = prepared(project, tmp_path)
    import poise.infrastructure.hook_transport as owner
    original = owner.atomic_write

    def interrupt(path, *args, **kwargs):
        if Path(path) == service.settings.hooks_file:
            raise OSError('source publication interrupted')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(owner, 'atomic_write', interrupt)
    with pytest.raises(OSError):
        service.reconfigure(packet)
    changed = json.loads(target.read_text())
    changed['operator_note'] = 'Newer operator change after partial publication.'
    write_json(target, changed)
    before = target.read_bytes()
    monkeypatch.setattr(owner, 'atomic_write', original)

    with pytest.raises(PoiseError):
        HookService(service.settings.path).reconfigure(packet)

    assert target.read_bytes() == before


@pytest.mark.parametrize('action', ['install', 'reconcile'])
def test_all_native_writers_use_the_persisted_shared_file_lock(project, tmp_path, action):
    service, packet, _, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    before = updated.settings.hooks_file.read_bytes()
    native_request = {'request_id': 'ordinary-' + action, 'expected_revision': updated.revision(),
                      'definition': packet['definition']}
    if action == 'reconcile':
        native_request.update(previous_installation_id='old-profile',
                              live_definition_path=receipt['definition_path'])

    with exclusive_lock(Path(packet['settings']['hooks_lock']), 3.0, 0.01):
        with pytest.raises(PoiseError, match='lock'):
            getattr(updated, action)(native_request)

    assert updated.settings.hooks_file.read_bytes() == before
