"""Reviewer regressions at the public CLI; native identities are unit fixtures."""
import io
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from conftest import git, write_json
from poise.common import PoiseError, load_config
from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from hook_transport.helpers import event, install, settings
from hook_transport.test_shared_session_entry import (
    assert_shared_state, cli, hook_state, prepared,
)


@pytest.mark.parametrize('control,git_repository', [
    ('GIT_DIR', True), ('GIT_DIR', False), ('GIT_COMMON_DIR', True),
])
def test_inherited_git_selection_cannot_deliver_to_unrelated_cwd(
        project, tmp_path, monkeypatch, control, git_repository):
    service, packet, _, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    unrelated = tmp_path / 'unrelated native cwd'
    unrelated.mkdir()
    if git_repository:
        git(unrelated, 'init')
    before = hook_state(updated)
    native = event(session='foreign-git-environment')
    native['cwd'] = str(unrelated)
    monkeypatch.setenv(control, str(project['app'] / '.git'))

    response = cli(updated, receipt['definition_path'], native, unrelated)

    assert response == {}
    assert hook_state(updated) == before


@pytest.mark.parametrize('control', ['GIT_DIR', 'GIT_COMMON_DIR'])
def test_fork_in_own_worktree_ignores_inherited_foreign_git_selection(
        project, tmp_path, monkeypatch, control):
    service, packet, _, _ = prepared(project, tmp_path)
    receipt = service.reconfigure(packet)
    updated = HookService(service.settings.path)
    worktree = tmp_path / 'own separate worktree'
    git(project['app'], 'worktree', 'add', '--detach', str(worktree), 'HEAD')
    foreign = tmp_path / 'foreign repository'
    foreign.mkdir()
    git(foreign, 'init')
    monkeypatch.setenv(control, str(foreign / '.git'))
    native = event(session='own-fork-with-git-environment')
    native.update(source='fork', cwd=str(worktree))

    response = cli(updated, receipt['definition_path'], native, worktree)

    binding = updated.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in response['hookSpecificOutput']['additionalContext']
    assert binding['external_session'] == native['session_id']


@pytest.mark.parametrize('error_size', [32, 10000])
def test_real_task_warning_preserves_entry_and_complete_diagnostic(project, tmp_path, error_size):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    config = json.loads(project['config_path'].read_text())
    config['workflows']['unmapped-' + 'x' * error_size] = next(iter(config['workflows'].values()))
    write_json(project['config_path'], config)
    with pytest.raises(PoiseError) as failure:
        load_config(project['config_path'])
    diagnostic = str(failure.value)
    assert (len(diagnostic) > service.settings.raw['output_chars']) == (error_size == 10000)
    native = event(session='real-task-warning')
    native['cwd'] = str(project['app'])
    output, error = io.StringIO(), io.StringIO()

    code = execute('hook', SimpleNamespace(settings=service.settings.path,
        definition=Path(installed['definition_path'])), io.BytesIO(json.dumps(native).encode()), output, error)

    assert code == 0, error.getvalue()
    assert not error.getvalue()
    assert len(output.getvalue()) <= service.settings.raw['output_chars']
    response = json.loads(output.getvalue())
    binding = service.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in response['hookSpecificOutput']['additionalContext']
    assert response['systemMessage']
    saved, = service.settings.observations.rglob(service.settings.raw['response_file'])
    full = json.loads(saved.read_text())
    assert full['systemMessage'] == diagnostic
    assert full['hookSpecificOutput'] == response['hookSpecificOutput']
    if error_size == 10000:
        assert str(saved) in response['systemMessage']
    else:
        assert response['systemMessage'] == diagnostic


def _interrupt_publication(service, packet, monkeypatch, point):
    import poise.infrastructure.hook_transport as owner
    original = owner.atomic_write
    binding = service.latest_binding('preserved-parent', 'primary')
    fault = {'source': service.settings.hooks_file,
             'binding': Path(binding['binding_path']),
             'settings': service.settings.path,
             'after-settings': service.settings.path}[point]
    failed = False

    def interrupt(path, *args, **kwargs):
        nonlocal failed
        if Path(path) == fault and not failed:
            failed = True
            if point == 'after-settings':
                original(path, *args, **kwargs)
            raise OSError('pending native publication interrupted')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(owner, 'atomic_write', interrupt)
    with pytest.raises(OSError, match='publication interrupted'):
        service.reconfigure(packet)
    assert failed
    monkeypatch.setattr(owner, 'atomic_write', original)


def _arrival(service, target, project):
    native = event(session='new-native-arrival')
    native['cwd'] = str(project['app'])
    document = json.loads(target.read_text())
    command, = [handler['command'] for group in document['hooks']['SessionStart']
                for handler in group['hooks']
                if str(service.settings.path) in handler.get('command', '')]
    result = subprocess.run(shlex.split(command), input=json.dumps(native),
                            capture_output=True, text=True, cwd=project['app'])
    assert result.returncode == 0, result.stderr
    current = HookService(service.settings.path)
    binding = current.latest_binding(native['session_id'], 'primary')
    assert binding['launcher'] in json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    prompt = event('UserPromptSubmit', session=native['session_id'])
    prompt['cwd'] = str(project['app'])
    current.event(binding['definition_path'], prompt)
    return current, binding


@pytest.mark.parametrize('point', ['source', 'binding', 'settings', 'after-settings'])
def test_exact_pending_transfer_adopts_legitimate_native_arrival(project, tmp_path, monkeypatch, point):
    service, packet, target, foreign = prepared(project, tmp_path)
    _interrupt_publication(service, packet, monkeypatch, point)
    current, arrival = _arrival(service, target, project)
    before = hook_state(current)
    assert len(before['bindings']) == 2
    assert all(row[2] is not None for row in before['bindings'])

    receipt = current.reconfigure(packet)

    recovered = assert_shared_state(service, packet, target, foreign, receipt)
    after = hook_state(recovered)
    assert after['events'][:len(before['events'])] == before['events']
    assert [(row[0], row[2]) for row in after['bindings']] == [(row[0], row[2]) for row in before['bindings']]
    binding = recovered.latest_binding(arrival['external_session'], 'primary')
    excluded = {'settings_digest', 'definition_path'}
    assert {key: value for key, value in binding.items() if key not in excluded} == {
        key: value for key, value in arrival.items() if key not in excluded}
    assert Path(binding['launcher']).is_file()
    assert json.loads(Path(binding['binding_path']).read_text()) == binding
    assert receipt['binding_count'] == 2
    assert recovered.reconfigure(packet) == receipt
    with sqlite3.connect(recovered.settings.database) as connection:
        operation = json.loads(connection.execute('SELECT data FROM operations WHERE id=?',
            (packet['request_id'],)).fetchone()[0])
    assert operation['phase'] == 'completed'
    assert 'files' not in operation


@pytest.mark.parametrize('changed', ['target', 'arrival-launcher'])
def test_arrival_recovery_preserves_newer_foreign_content(project, tmp_path, monkeypatch, changed):
    service, packet, target, _ = prepared(project, tmp_path)
    _interrupt_publication(service, packet, monkeypatch, 'source')
    current, arrival = _arrival(service, target, project)
    if changed == 'target':
        document = json.loads(target.read_text())
        document['operator_note'] = 'Newer foreign content must survive.'
        write_json(target, document)
        path = target
    else:
        path = Path(arrival['launcher'])
        path.write_text('foreign launcher content\n')
    before = path.read_bytes(), hook_state(current)

    with pytest.raises(PoiseError):
        current.reconfigure(packet)

    assert (path.read_bytes(), hook_state(current)) == before


UNICODE_WARNINGS = [
    ('high-surrogate', '\ud800', 'Неоднозначный workflow unmapped-\ud800'),
    ('low-surrogate', '\udc80', 'Неоднозначный workflow unmapped-\udc80'),
    ('supplementary', '\U00010000', 'Неоднозначный workflow unmapped-\U00010000'),
]


def _unicode_warning_cli(project, tmp_path, kind, text):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    config = json.loads(project['config_path'].read_text())
    config['workflows']['unmapped-' + text] = next(iter(config['workflows'].values()))
    project['config_path'].write_text(json.dumps(config, ensure_ascii=True), encoding='utf-8')
    native = event(session='unicode-warning-' + kind)
    native['cwd'] = str(project['app'])
    command = service.settings.command('hook', '--definition', installed['definition_path'])
    completed = subprocess.run(shlex.split(command), input=json.dumps(native).encode('utf-8'),
                               capture_output=True, cwd=project['app'])
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == b''
    response = json.loads(completed.stdout.decode('utf-8', errors='strict'))
    binding = service.latest_binding(native['session_id'], 'primary')
    assert binding['external_session'] == native['session_id']
    assert binding['launcher'] in response['hookSpecificOutput']['additionalContext']
    assert response['systemMessage']
    return service, response


@pytest.mark.parametrize('kind,text,diagnostic', UNICODE_WARNINGS)
def test_unicode_task_warning_has_valid_native_wire(project, tmp_path, kind, text, diagnostic):
    _, response = _unicode_warning_cli(project, tmp_path, kind, text)

    assert response['systemMessage'] == diagnostic


@pytest.mark.parametrize('kind,text,diagnostic', UNICODE_WARNINGS)
def test_unicode_task_warning_has_complete_utf8_json_diagnostic(project, tmp_path, kind, text, diagnostic):
    service, response = _unicode_warning_cli(project, tmp_path, kind, text)

    saved, = service.settings.observations.rglob(service.settings.raw['response_file'])
    full = json.loads(saved.read_bytes().decode('utf-8', errors='strict'))
    assert full['systemMessage'] == diagnostic
    assert full['hookSpecificOutput'] == response['hookSpecificOutput']
