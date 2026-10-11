"""Actual project relocation must preserve existing immutable evidence consumers."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from poise.common import PoiseError, load_config
from projects.test_update import project_revision, update_tools

from .material_relocation_helpers import (
    STDERR, STDOUT, checksum, configured_hook, fresh, immutable, placed, prepare, relocate, required,
)


def test_receipt_readback_after_source_retirement(project, tmp_path):
    case = prepare(project, tmp_path)
    receipt = deepcopy(case['receipt'])
    moved = relocate(case)
    views = moved.runtime.result_views.command_views([receipt])
    assert len(views) == 1
    assert 'presentation_error' not in views[0], views
    assert STDOUT.decode().strip() in views[0]['primary']
    assert STDERR.decode().strip() in views[0]['primary']
    assert views[0]['id'] == receipt['id']
    assert views[0]['method'] == 'READ_WHEEL'
    assert views[0]['actual_exit_code'] == 0 and views[0]['passed'] is True
    assert required(moved.runtime.evidence_commands.list_for, 'T1')[0] == receipt
    assert not case['source'].exists(), 'Readback must not recreate the retired source'


def test_relocation_preserves_identity_history_and_independent_roots(project, tmp_path):
    case = prepare(project, tmp_path)
    before = immutable(case['tools'].runtime)
    records = case['tools'].runtime.store.artifact_records('T1')
    _, config, _ = load_config(case['config_path'])
    other_roots = {key: config['paths'][key] for key in (
        'worktrees', 'requirements_database', 'requirements_lock')}
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    _, current, _ = load_config(case['config_path'])
    assert {key: current['paths'][key] for key in other_roots} == other_roots
    assert current['git']['repository'] == str(case['project']['app'])
    actual = moved.runtime.store.artifact_records('T1')
    assert {row['id'] for row in actual} == {row['id'] for row in records}
    for old in records:
        row = next(row for row in actual if row['id'] == old['id'])
        assert {key: row[key] for key in ('owner', 'scope', 'digest')} == {
            key: old[key] for key in ('owner', 'scope', 'digest')}
        assert Path(row['path']) == placed(case, old['path'])
        assert checksum(Path(row['path']).read_bytes()) == old['digest']


@pytest.mark.parametrize('seam', [
    'copy_state_tree', 'publish_state_tree', 'switch_project_config', 'remove_source_state',
])
def test_exact_retry_after_interrupted_effect_preserves_readback(project, tmp_path, monkeypatch, seam):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    before = immutable(case['tools'].runtime)
    effect = getattr(owner, seam)
    observed = []

    def interrupted(*args, **kwargs):
        effect(*args, **kwargs)
        observed.append(seam)
        raise OSError('test interruption after ' + seam)

    with monkeypatch.context() as patch:
        patch.setattr(owner, seam, interrupted)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    assert observed == [seam]
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])
    assert not case['source'].exists()
    assert required(update_tools(case['settings']).apply, case['packet'])['replayed'] is True
    assert immutable(fresh(case).runtime) == before


def test_retry_after_real_destination_registry_change(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    records = case['tools'].runtime.store.artifact_records('T1')

    def interrupted(source):
        raise OSError('test interruption before source retirement')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'remove_source_state', interrupted)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    assert case['source'].is_dir() and case['destination'].is_dir()
    moved = fresh(case)
    record = moved.runtime.task_queries.record('T1')
    # Real public artifact recovery changes destination SQLite after the copy.
    recovered = required(moved.invoke, request('recover_artifacts', {
        'request_id': 'recover-before-retire', 'task_id': 'T1',
        'expected_version': record['_version'],
        'artifact_ids': [row['id'] for row in records],
        'source_roots': {'task': case['context']['task_root']},
        'reason': 'Finish admitted relocation material publication.',
        'authorization': 'The disposable project owner approves exact rehoming.',
    }))
    assert recovered['status'] == 'artifacts_recovered'
    destination_records = moved.runtime.store.artifact_records('T1')
    old_db = case['source'] / case['project']['cfg']['paths']['database']
    new_db = case['destination'] / case['project']['cfg']['paths']['database']
    assert old_db.read_bytes() != new_db.read_bytes(), 'Regression needs changed destination metadata'
    resumed = relocate(case)
    assert resumed.runtime.store.artifact_records('T1') == destination_records
    assert resumed.runtime._receipt_files_intact(case['receipt'], case['method'])


def test_recovery_rejects_foreign_live_claim_without_seizing_it(project, tmp_path):
    case = prepare(project, tmp_path, released=False)
    observer = fresh(case, 'foreign-observer')
    before = immutable(case['tools'].runtime)
    records = case['tools'].runtime.store.artifact_records('T1')
    current = observer.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='released Task claim'):
        observer.invoke(request('recover_artifacts', {
            'request_id': 'foreign-recovery', 'task_id': 'T1',
            'expected_version': current['_version'],
            'artifact_ids': [row['id'] for row in records],
            'source_roots': {'task': case['context']['task_root']},
            'reason': 'Probe explicit writer admission.', 'authorization': 'No claim revocation.',
        }))
    assert immutable(case['tools'].runtime) == before
    assert observer.runtime.task_queries.record('T1')['claimed_by'] == 'producer'
    assert case['marker'].read_text() == 'run\n'


def test_occupied_destination_is_not_overwritten(project, tmp_path):
    case = prepare(project, tmp_path)
    case['destination'].mkdir()
    sentinel = case['destination'] / 'operator.txt'
    sentinel.write_bytes(b'operator data\n')
    before = immutable(case['tools'].runtime)
    with pytest.raises(PoiseError):
        update_tools(case['settings']).apply(case['packet'])
    assert sentinel.read_bytes() == b'operator data\n'
    assert case['source'].is_dir()
    assert immutable(case['tools'].runtime) == before


def test_retired_terminal_history_needs_no_deleted_proof_bytes(project, tmp_path):
    from terminal_materials.helpers import agree, cancel_and_discard
    case = prepare(project, tmp_path)
    tools = case['tools']
    required(tools.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    agree(tools, {'disposition': 'no_result', 'outputs': []})
    required(cancel_and_discard, tools, case['done']['commit'])
    assert not Path(case['context']['task_root']).exists()
    before = immutable(tools.runtime)
    moved = relocate(case)
    terminal = required(moved.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert terminal['status'] == 'cancelled' and terminal['result_template'] is None
    shown = moved.runtime.result_views.present_result(
        {'checks': [case['receipt']]}, moved.runtime.retired_receipt,
        moved.runtime.task_queries.receipt_task,
    )
    # The test uses actual owning callbacks and an actually completed retirement.
    assert moved.runtime.delivery_tools.state('T1').data['retirement_started'] is True
    assert 'Служебные журналы уничтожены' in str(shown)
    assert immutable(moved.runtime) == before
    assert not placed(case, case['receipt']['stdout']).exists()


def test_delivered_registered_input_still_resolves_permanent_bytes(project, tmp_path):
    from poise.application.artifact_materials import ArtifactMaterialResolver
    from terminal_materials.helpers import agree, cancel_and_discard
    from .material_relocation_helpers import INPUT
    case = prepare(project, tmp_path)
    tools = case['tools']
    required(tools.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    record = next(row for row in tools.runtime.store.artifact_records('T1')
                  if row['path'].endswith('/artifacts/inputs/accepted.whl'))
    permanent = tmp_path / 'permanent-input.whl'
    agree(tools, {'disposition': 'deliver', 'outputs': [{
        'id': 'accepted-input', 'kind': 'file', 'source': record['path'],
        'destination': str(permanent), 'digest': checksum(INPUT),
    }]})
    required(cancel_and_discard, tools, case['done']['commit'])
    assert permanent.read_bytes() == INPUT
    assert not Path(record['path']).exists()
    moved = relocate(case)
    material = required(ArtifactMaterialResolver(moved.runtime.delivery_tools).resolve,
                        'T1', [record])[record['id']]
    assert material.artifact_id == record['id']
    assert material.digest == checksum(INPUT)
    assert material.physical_path == str(permanent)
    assert Path(material.physical_path).read_bytes() == INPUT
    assert not placed(case, record['path']).exists()


def test_actual_hook_writer_lock_blocks_retirement(project, tmp_path):
    from hook_transport.helpers import install
    from poise.infrastructure.locking import exclusive_lock
    case = prepare(project, tmp_path, native_hooks=True)
    hook = configured_hook(case, tmp_path)
    install(hook)
    case['packet']['expected_revision'] = project_revision(case['config_path'])
    config_bytes = case['config_path'].read_bytes()
    hooks_bytes = hook.settings.hooks_file.read_bytes()
    before = immutable(case['tools'].runtime)
    # This is an actual held operational lock, not an inferred live Task status.
    with exclusive_lock(hook.settings.lock, 1.0, 0.01):
        error = None
        try:
            update_tools(case['settings']).apply(case['packet'])
        except PoiseError as exc:
            error = exc
        assert error is not None, 'Relocation ignored an actually held hook writer lock'
        assert any(word in str(error).lower() for word in ('lock', 'writer', 'hook', 'binding', 'runtime'))
    assert case['source'].is_dir()
    assert case['config_path'].read_bytes() == config_bytes
    assert hook.settings.hooks_file.read_bytes() == hooks_bytes
    assert immutable(case['tools'].runtime) == before


def test_stale_native_hook_cannot_recreate_retired_source(project, tmp_path):
    from hook_transport.helpers import event, install
    case = prepare(project, tmp_path, native_hooks=True)
    hook = configured_hook(case, tmp_path)
    installed = install(hook)
    case['packet']['expected_revision'] = project_revision(case['config_path'])
    moved = relocate(case)
    native = event(session='after-state-move')
    native['cwd'] = str(case['project']['root'])
    try:
        hook.event(installed['definition_path'], native)
    except PoiseError as exc:
        assert str(exc), 'A stale binding/settings refusal must explain its cause'
    assert not case['source'].exists(), 'A stale native hook recreated the retired state'
    assert moved.runtime.evidence_commands.list_for('T1')[0] == case['receipt']


def test_changed_pending_intent_is_refused_without_retiring_source(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)

    def stop_before_retirement(source):
        raise OSError('test interruption before retirement')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'remove_source_state', stop_before_retirement)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    before = immutable(fresh(case).runtime)
    conflict = deepcopy(case['packet'])
    conflict['state_relocation']['source_disposition'] = 'retain'
    with pytest.raises(PoiseError, match='Different request|conflict'):
        update_tools(case['settings']).apply(conflict)
    assert case['source'].is_dir() and case['destination'].is_dir()
    assert immutable(fresh(case).runtime) == before
