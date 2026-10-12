"""Independent regression obligations from interrupted real-owner relocation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import track_runtime_dispatchers
from poise.common import PoiseError
from hook_transport.helpers import install
from .material_relocation_helpers import configured_hook, fresh, immutable, prepare
from .test_material_relocation_coordination import native
from .test_update import update_tools


def stop_after_copy(case, monkeypatch):
    from poise.infrastructure.sqlite.artifacts import SqliteArtifactRepository
    observed = []

    def interrupted(*args, **kwargs):
        observed.append('rebind')
        raise OSError('Interruption after copied phase')

    with monkeypatch.context() as patch:
        patch.setattr(SqliteArtifactRepository, 'rebind', interrupted)
        with pytest.raises(PoiseError, match='interrupted'):
            update_tools(case['settings']).apply(case['packet'])
    assert observed == ['rebind']
    assert case['source'].is_dir() and case['destination'].is_dir()


def retry(case):
    try:
        return update_tools(case['settings']).apply(case['packet'])
    except PoiseError as exc:
        return {'refused': str(exc)}


def audit_count(database):
    with sqlite3.connect(database) as db:
        return db.execute('SELECT COUNT(*) FROM journal WHERE event=?',
                          ('reviewer.intervening',)).fetchone()[0]


def test_resumed_copy_preserves_intervening_owned_audit(project, tmp_path, monkeypatch):
    case = prepare(project, tmp_path)
    stop_after_copy(case, monkeypatch)
    before = immutable(case['tools'].runtime)
    case['tools'].runtime.store.event(
        'reviewer-late-writer', 'T1', 'reviewer.intervening', {'marker': 'after-copy'})
    database = case['project']['cfg']['paths']['database']
    assert audit_count(case['source'] / database) == 1
    result = retry(case)
    assert 'refused' in result or audit_count(case['destination'] / database) == 1, (
        'Accepted source history was deleted without publication')
    if 'refused' in result:
        assert result['refused'].strip() and case['source'].is_dir()
        assert audit_count(case['source'] / database) == 1
        assert immutable(case['tools'].runtime) == before
    else:
        assert result['state_relocation']['status'] == 'completed'
        assert immutable(fresh(case).runtime) == before


def test_resumed_move_rechecks_new_live_hook_binding(project, tmp_path, monkeypatch):
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    installed = install(service)
    stop_after_copy(case, monkeypatch)
    native(service, installed['definition_path'], case)
    binding = service.latest_binding('fixture-hook-owner', 'primary')
    assert service.registry.liveness(binding['session_id']).value == 'live'
    binding_path = Path(binding['binding_path'])
    binding_bytes = binding_path.read_bytes()
    before = immutable(case['tools'].runtime)
    result = retry(case)
    assert 'refused' in result and case['source'].is_dir(), (
        'Resume retired a newly LIVE native owner')
    assert result['refused'].strip()
    assert binding_path.read_bytes() == binding_bytes
    assert service.registry.liveness(binding['session_id']).value == 'live'
    assert immutable(case['tools'].runtime) == before


def test_resumed_destination_preserves_unregistered_source_bytes(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as effects
    case = prepare(project, tmp_path)
    original = case['source'] / 'operator-working.bin'
    original.write_bytes(b'owned original bytes')
    before = immutable(case['tools'].runtime)

    def stopped(*args):
        raise OSError('Interruption before retirement')

    with monkeypatch.context() as patch:
        patch.setattr(effects, 'remove_source_state', stopped)
        with pytest.raises(PoiseError, match='interrupted'):
            update_tools(case['settings']).apply(case['packet'])
    copied = case['destination'] / 'operator-working.bin'
    assert copied.read_bytes() == b'owned original bytes'
    copied.write_bytes(b'changed destination bytes')
    result = retry(case)
    assert 'refused' in result and original.is_file(), (
        'Conflicting destination bytes authorized deleting the sole original')
    assert result['refused'].strip()
    assert original.read_bytes() == b'owned original bytes'
    assert copied.read_bytes() == b'changed destination bytes'
    assert immutable(case['tools'].runtime) == before


@pytest.mark.parametrize('fault', [
    'null-source',
    'list-disposition',
    'null-hook',
    'missing-hook-fields',
])
def test_versioned_relocation_rejects_typed_invalid_input_as_domain_result(
        project, tmp_path, monkeypatch, request, fault):
    dispatchers = track_runtime_dispatchers(monkeypatch)
    case = prepare(project, tmp_path)
    # Every runtime created by prepare owns a real asynchronous writer. Drain
    # all of them before the full byte baseline; retain telemetry in that proof.
    assert dispatchers
    for dispatcher in dispatchers:
        request.addfinalizer(dispatcher.close)
        dispatcher.flush()
    marker = case['source'] / 'operator-working.bin'
    marker.write_bytes(b'unchanged working data\x00\xff')

    def source_files():
        return {str(path.relative_to(case['source'])): path.read_bytes()
                for path in case['source'].rglob('*') if path.is_file()}

    source_bytes = source_files()
    config_bytes = case['config_path'].read_bytes()

    def invoke(packet):
        return subprocess.run(
            [sys.executable, '-B', '-m', 'poise', 'project-config', '--settings', str(case['settings'])],
            input=json.dumps(packet), capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': str(Path.cwd() / 'src')})

    def assert_preserved():
        assert source_files() == source_bytes
        assert case['config_path'].read_bytes() == config_bytes
        assert not case['destination'].exists()

    # A known-correct domain refusal proves this complete snapshot is compatible
    # with the requested result before testing the missing nested validation.
    control_packet = deepcopy(case['packet'])
    control_packet['state_relocation'] = 'invalid-object'
    control = invoke(control_packet)
    assert control.returncode == 2 and control.stderr == ''
    control_result = json.loads(control.stdout)
    assert control_result['status'] == 'rejected'
    assert 'state_relocation' in control_result['reason']
    assert_preserved()

    packet = deepcopy(case['packet'])
    move = packet['state_relocation']
    if fault == 'null-source':
        move['expected_source'] = None
        field = 'expected_source'
    elif fault == 'list-disposition':
        move['source_disposition'] = []
        field = 'source_disposition'
    elif fault == 'null-hook':
        move['hook_destinations'] = [None]
        field = 'hook_destinations'
    else:
        move['hook_destinations'] = [{'installation_id': 'broken'}]
        field = 'settings_path'
    process = invoke(packet)
    assert_preserved()
    assert process.returncode == 2 and process.stderr == '', (
        'Typed bad request escaped the public structured refusal boundary: '
        + process.stderr[-300:])
    result = json.loads(process.stdout)
    assert result['status'] == 'rejected'
    assert field in result['reason'], 'Refusal must identify the unusable request field'


def test_retry_after_source_retirement_does_not_recreate_old_hook_root(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as effects
    from poise.application.hook_transport import HookCommands
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    install(service)
    before = immutable(case['tools'].runtime)
    remove = effects.remove_source_state
    retired = []

    def removed_then_interrupted(source):
        remove(source)
        retired.append('removed')
        raise OSError('Interruption after irreversible retirement')

    with monkeypatch.context() as patch:
        patch.setattr(effects, 'remove_source_state', removed_then_interrupted)
        with pytest.raises(PoiseError, match='interrupted'):
            update_tools(case['settings']).apply(case['packet'])
    assert retired == ['removed'] and not case['source'].exists()

    def interrupted_hook(*args, **kwargs):
        raise OSError('Interrupt a later retry if it repeats hook publication')

    with monkeypatch.context() as patch:
        patch.setattr(HookCommands, 'relocate', interrupted_hook)
        second = retry(case)
    assert not case['source'].exists(), 'Retry recreated the retired source hook root'
    third = retry(case)
    assert 'refused' not in third, 'Exact retry became unrecoverable after actual retirement'
    assert third['state_relocation']['status'] == 'completed'
    assert not case['source'].exists()
    assert immutable(fresh(case).runtime) == before
    assert second.get('refused') or second['state_relocation']['status'] == 'completed'
