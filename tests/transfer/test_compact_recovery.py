"""Compact delivery contract at configuration, transfer and Task boundaries.

These are future-product tests. A schema refusal does not prove assertions below
that refusal; evidence must distinguish reached failures from blocked scenarios.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tarfile

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError
from poise.modules.transfers.domain import TransferPolicy, TransferRequest, validate_saved_work

from .compact_helpers import configure, flow, prepared, registered_note, save, target
from .helpers import export, handoff_args, pick, restore
from .test_paths import prepared as legacy_prepared


def test_guard_request_selection_and_import_identity_remain_explicit():
    packet = {'action': 'export', 'request_id': 'bounded', 'task_ids': ['T1'],
              'sprint_id': None, 'handoff': None}
    assert TransferRequest.parse(packet, {'max_tasks': 2}).data == packet
    duplicated = {**packet, 'task_ids': ['T1', 'T1']}
    with pytest.raises(PoiseError, match='Duplicate'):
        TransferRequest.parse(duplicated, {'max_tasks': 2})
    invalid = {'action': 'import', 'request_id': 'bad-digest',
               'package_path': '/disposable/package', 'package_digest': 'not-a-hash'}
    with pytest.raises(PoiseError, match='SHA-256'):
        TransferRequest.parse(invalid, {'max_tasks': 2})


def test_guard_foreign_ownership_cannot_be_invented_as_released():
    record = {'id': 'FOREIGN', 'status': 'active', 'claimed_by': 'other-session'}
    with pytest.raises(PoiseError, match='still owned'):
        validate_saved_work([record], {'FOREIGN'})
    assert record == {'id': 'FOREIGN', 'status': 'active', 'claimed_by': 'other-session'}
    unclaimed = {**record, 'claimed_by': None}
    with pytest.raises(PoiseError, match='explicit released handoff'):
        validate_saved_work([unclaimed], set())
    validate_saved_work([unclaimed], {'FOREIGN'})


def archive_contents(saved):
    """Independent reader; never creates the product's archive or manifest."""
    with tarfile.open(saved['package_path'], 'r:gz') as archive:
        files = [item for item in archive.getmembers() if item.isfile()]
        names = [item.name.removeprefix('./') for item in files]
        assert len(names) == len(set(names))
        entries = {name: archive.extractfile(item).read()
                   for name, item in zip(names, files, strict=True)}
    return entries, json.loads(entries['manifest.json'])


def test_missing_recovery_contract_cannot_silently_export_source_history(project):
    # Valid existing configuration reaches the actual export, so a fixture or
    # unavailable executable cannot produce this intended missing-denial RED.
    with pytest.raises(PoiseError, match='(?i)(recovery.*required|required.*recovery)'):
        _, tools, _, payload = legacy_prepared(project)
        export(tools, handoff=handoff_args(payload))


def test_explicit_recovery_contract_is_accepted_without_legacy_bundle_path(
    project, recovery_tool,
):
    configure(project, recovery_tool)
    raw = project['cfg']['runtime_services']['transfer']
    try:
        parsed = TransferPolicy.parse(raw)
    except DomainError as exc:
        expected = (Path(__file__).parent / 'fixtures' / 'legacy-transfer-refusal.txt').read_text().strip()
        if str(exc) != expected:
            raise
        pytest.fail('Explicit recovery configuration contract is not implemented')
    assert parsed.data == raw
    assert 'bundles_directory' not in parsed.data
    # The real project loader is a separate consumer of the same new contract.
    assert WorkPoise(project['config_path'], 'config-consumer').cfg[
        'runtime_services']['transfer'] == raw


@pytest.mark.parametrize('key', [
    'tool_argv', 'mapping', 'systems', 'paths', 'create_flow',
    'restore_flow', 'component_steps', 'placement_decisions', 'format', 'repositories',
])
def test_recovery_inputs_are_required_not_silently_defaulted(
    project, recovery_tool, key,
):
    configure(project, recovery_tool)
    raw = deepcopy(project['cfg']['runtime_services']['transfer'])
    # First prove the positive configuration is accepted. An unrelated refusal
    # cannot make a missing-field negative case appear to pass.
    TransferPolicy.parse(raw)
    del raw['recovery'][key]
    with pytest.raises(PoiseError, match=key):
        TransferPolicy.parse(raw)


def test_actual_workspace_recover_archive_extract_and_failure_observations(
    recovery_tool, tmp_path,
):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note').write_bytes(b'Independent tool fixture\n')
    manifest = write_json(tmp_path / 'flow.json', {
        'schema': 'workspace-recover/flow/v3', 'workspace': str(tmp_path),
        'steps': [
            {'id': 'archive', 'op': 'archive', 'from': str(source),
             'to': str(tmp_path / 'tool.tar.gz')},
            {'id': 'extract', 'op': 'extract', 'from': str(tmp_path / 'tool.tar.gz'),
             'to': str(tmp_path / 'restored')},
            {'id': 'failed', 'op': 'command',
             'argv': [sys.executable, '-B', '-c', 'raise SystemExit(7)'],
             'onError': 'continue'},
        ],
    })
    state = flow(recovery_tool, manifest, tmp_path / 'session')
    assert (tmp_path / 'restored' / 'note').read_bytes() == b'Independent tool fixture\n'
    failed = next(item for item in state['results'] if item['id'] == 'failed')
    assert failed['status'] == 'failed'
    assert failed['exitCode'] == 7
    assert source.is_dir()


def test_compact_archive_uses_roles_and_omits_source_history_and_reproducibles(
    project, recovery_tool,
):
    runtime, tools, context, payload = prepared(project, recovery_tool)
    note = registered_note(tools)
    root = Path(context['task_root'])
    # Intentionally misleading names: semantics come from explicit mapping.
    for name, data in [('important', b'OLD BACKUP'), ('transcript', b'OLD OUTPUT'),
                       ('installed', b'DEPENDENCY'), ('generated', b'GENERATED')]:
        (root / name).write_bytes(data)
    before = note.read_bytes()
    saved = save(tools, payload)
    entries, manifest = archive_contents(saved)
    assert manifest['format'] == 'poise-recovery-1'
    assert manifest['mapping']['version'] == 'fixture-v1'
    assert set(entries) == {'manifest.json'} | {item['path'] for item in manifest['files']}
    assert {item['path'] for item in manifest['files'] if item['role'] == 'task-material'} == {
        'materials/T1/notes/cache',
    }
    assert all(item['role'] in {'task-state', 'task-material', 'configuration',
                               'code-diff', 'proof-provenance'} for item in manifest['files'])
    assert entries['materials/T1/notes/cache'] == b'Necessary task material\n'
    assert note.read_bytes() == before
    assert not Path(saved['package_path']).is_relative_to(root)
    assert not any(data.startswith((b'# v2 git bundle\n', b'# v3 git bundle\n'))
                   for data in entries.values())
    assert not any(Path(name).parts[0] in {'source', '.git', 'bundles'}
                   for name in entries)
    assert not {b'OLD BACKUP', b'OLD OUTPUT', b'DEPENDENCY', b'GENERATED'} & set(entries.values())
    assert manifest['workspaces']['T1']['commit'] == git(Path(context['worktree']), 'rev-parse', 'HEAD')
    assert runtime.current_task() is None


def test_dirty_code_roundtrip_carries_text_binary_additions_and_deletions(
    project, recovery_tool, tmp_path,
):
    baseline = project['app'] / 'tests'
    baseline.mkdir()
    (baseline / 'tracked.txt').write_bytes(b'Before text change\n')
    (baseline / 'binary.bin').write_bytes(b'\x00\xffold\x00')
    (baseline / 'deleted.txt').write_bytes(b'Before deletion\n')
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Tracked WIP inputs')
    git(project['app'], 'push', 'backup', 'main')
    _, tools, context, payload = prepared(project, recovery_tool)
    tree = Path(context['worktree'])
    # This fixture Task owns tests/**; code WIP uses its allowed test surface.
    (tree / 'tests' / 'new.txt').write_bytes(b'Unsaved addition\n')
    (tree / 'tests' / 'tracked.txt').write_bytes(b'Unsaved text change\n')
    (tree / 'tests' / 'binary.bin').write_bytes(b'\x00\xff\x01new\x00')
    (tree / 'tests' / 'deleted.txt').unlink()
    saved_head = git(tree, 'rev-parse', 'HEAD')
    original_main = git(project['app'], 'rev-parse', 'main')
    saved = save(tools, payload)
    entries, manifest = archive_contents(saved)
    assert manifest['workspaces']['T1']['commit'] == saved_head
    assert manifest['workspaces']['T1']['diff'] in entries
    assert entries[manifest['workspaces']['T1']['diff']]
    assert not any(data.startswith(b'# v2 git bundle\n') for data in entries.values())
    _, receiver = target(project, tmp_path / 'destination')
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    current = pick(receiver, 'T1')
    restored = Path(current['worktree'])
    assert (restored / 'tests' / 'new.txt').read_bytes() == b'Unsaved addition\n'
    assert (restored / 'tests' / 'tracked.txt').read_bytes() == b'Unsaved text change\n'
    assert (restored / 'tests' / 'binary.bin').read_bytes() == b'\x00\xff\x01new\x00'
    assert not (restored / 'tests' / 'deleted.txt').exists()
    assert git(restored, 'rev-parse', 'HEAD') == saved_head
    assert git(restored, 'status', '--porcelain=v1')
    assert git(project['app'], 'rev-parse', 'main') == original_main


def test_different_mapping_requires_recorded_agent_placement_not_name_guessing(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    registered_note(tools)
    saved = save(tools, payload)
    mapping = deepcopy(project['cfg']['runtime_services']['transfer']['recovery']['mapping'])
    mapping['version'] = 'fixture-v2'
    mapping['materials'][0]['destination'] = 'documents/current-note'
    dst, receiver = target(project, tmp_path / 'destination', mapping=mapping)
    waiting = restore(receiver, saved['package_path'], saved['package_digest'])
    assert waiting['status'] == 'placement_decision_required'
    assert receiver.runtime.task_queries.record('T1') is None
    decision = {'source_version': 'fixture-v1', 'target_version': 'fixture-v2',
                'reason': 'Current Task material contract uses documents/',
                'destinations': {'note': 'documents/current-note'}}
    dst['cfg']['runtime_services']['transfer']['recovery']['placement_decisions'] = [decision]
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    deployed = restore(receiver, saved['package_path'], saved['package_digest'], request_id='resolved-placement')
    assert deployed['success'] is True
    assert deployed['placement_decision'] == decision
    context = pick(receiver, 'T1')
    assert (Path(context['task_root']) / 'documents/current-note').read_bytes() == b'Necessary task material\n'
    assert not (Path(context['task_root']) / 'notes' / 'cache').exists()


def test_two_full_config_sets_are_captured_at_check_time_not_export_time(
    project, recovery_tool,
):
    runtime, tools, _, payload = prepared(project, recovery_tool)
    cfg = project['cfg']['runtime_services']['transfer']['recovery']['systems']
    expected = {owner: {path: Path(path).read_bytes() for path in spec['configs']}
                for owner, spec in cfg.items()}
    commits = {owner: git(Path(spec['repository']), 'rev-parse', 'HEAD')
               for owner, spec in cfg.items()}
    checked = verify(tools, payload)
    assert checked['status'] == 'verified'
    for owner in ('poise', 'project'):
        Path(cfg[owner]['configs'][1]).write_text('{"changed_after_check":true}\n')
    saved = save(tools)
    entries, manifest = archive_contents(saved)
    proof = next(item for item in manifest['proofs']
                 if item['receipt_id'] == checked['checks'][0]['id'])
    assert set(proof['systems']) == {'poise', 'project'}
    for owner in ('poise', 'project'):
        actual = proof['systems'][owner]
        assert actual['commit'] == commits[owner]
        assert {item['path']: entries[item['archive_path']] for item in actual['configs']} == expected[owner]
    assert not any(item['role'] == 'historical-output' for item in manifest['files'])
    assert runtime.current_task() is None


def test_deploy_preserves_current_stage_and_fresh_verification_needs_no_old_logs(
    project, recovery_tool, tmp_path,
):
    runtime, tools, context, payload = prepared(project, recovery_tool)
    note = registered_note(tools)
    payload['artifact_paths'] = [str(note)]
    checked = verify(tools, payload)
    original = runtime.task_queries.record('T1')
    saved = save(tools)
    saved_history = runtime.task_queries.record('T1')['history']
    _, receiver = target(project, tmp_path / 'destination')
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    imported = receiver.runtime.task_queries.record('T1')
    assert imported['stage_index'] == original['stage_index']
    assert imported['iteration'] == original['iteration']
    assert imported['history'][:len(saved_history)] == saved_history
    assert receiver.runtime.current_task() is None
    historical = receiver.runtime.evidence_commands.list_for('T1')
    assert {item['id'] for item in historical} == {item['id'] for item in checked['checks']}
    assert all(not Path(item['stdout']).is_file() and not Path(item['stderr']).is_file()
               for item in historical)
    assert all(Path(item['stdout']).is_file() and Path(item['stderr']).is_file()
               for item in checked['checks'])
    # This acquisition explicitly authorizes subsequent work; deployment above
    # must not have executed checks, acquired the Task or advanced its stage.
    resumed = pick(receiver, 'T1')
    assert resumed['stage'] == context['stage']
    assert resumed['status'] == 'active'
    assert len(resumed['result_template']['artifact_paths']) == 1
    assert all(Path(path).is_file() for path in resumed['result_template']['artifact_paths'])
    assert Path(resumed['result_template']['artifact_paths'][0]).read_bytes() == b'Necessary task material\n'
    fresh = verify(receiver, result(resumed, 'Fresh current-stage verification'))
    assert fresh['status'] == 'verified'
    assert not fresh['replayed']
    assert {item['id'] for item in fresh['checks']}.isdisjoint(item['id'] for item in checked['checks'])
    progressed = receiver.invoke(request('advance', {
        'task_id': 'T1', 'request_id': 'fresh-authorized-next',
        'target_stage': 'test_review',
    }))
    assert progressed['stage'] == 'test_review'


def test_task_components_and_replayed_import_do_not_reinstall_whole_system(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    counter = tmp_path / 'component-calls'
    system = tmp_path / 'installed-system'
    system.write_bytes(b'Existing shared environment\n')
    script = Path(__file__).parent / 'fixtures' / 'task_component.py'
    package = tmp_path / 'installed-package'
    package.write_bytes(b'Compatible installed task package\n')
    component = {'id': 'task-only', 'op': 'command',
                 'argv': [sys.executable, '-B', str(script), '${variables.task_root}', str(counter)],
                 'onError': 'stop'}
    copy_component = {'id': 'reuse-installed', 'op': 'copy', 'from': str(package),
                      'to': '${variables.task_root}/generated/package'}
    dst, receiver = target(project, tmp_path / 'destination', components=[component, copy_component])
    first = restore(receiver, saved['package_path'], saved['package_digest'])
    assert first['success'] is True
    task = receiver.runtime.task_queries.record('T1')
    context = pick(receiver, 'T1')
    assert (Path(context['task_root']) / 'generated' / 'ready').read_bytes() == b'Regenerated task component\n'
    assert (Path(context['task_root']) / 'generated' / 'package').read_bytes() == b'Compatible installed task package\n'
    second = restore(receiver, saved['package_path'], saved['package_digest'])
    assert second['replayed'] is True
    assert counter.read_text() == 'called\n'
    assert system.read_bytes() == b'Existing shared environment\n'
    assert package.read_bytes() == b'Compatible installed task package\n'
    assert receiver.runtime.task_queries.record('T1')['stage_index'] == task['stage_index']
    assert git(dst['app'], 'rev-parse', 'main') == git(project['app'], 'rev-parse', 'main')


def test_failed_component_never_reports_success_or_runs_following_mutations(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    marker = tmp_path / 'must-not-run'
    steps = [
        {'id': 'failure', 'op': 'command',
         'argv': [sys.executable, '-B', '-c', 'raise SystemExit(9)'], 'onError': 'stop'},
        {'id': 'following', 'op': 'write', 'to': str(marker), 'content': 'incorrect'},
    ]
    _, receiver = target(project, tmp_path / 'destination', components=steps)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is False
    assert deployed['status'] == 'failed'
    assert any(step['id'] == 'failure' and step['exit_code'] == 9 for step in deployed['steps'])
    assert not marker.exists()
    assert receiver.runtime.current_task() is None


def test_flow_completion_with_failed_required_command_is_not_deploy_success(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    marker = tmp_path / 'explicit-following-step'
    components = [
        {'id': 'failed-required', 'op': 'command',
         'argv': [sys.executable, '-B', '-c', 'raise SystemExit(11)'], 'onError': 'continue'},
        {'id': 'allowed-following', 'op': 'write', 'to': str(marker), 'content': 'observed'},
    ]
    _, receiver = target(project, tmp_path / 'destination', components=components)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert marker.read_text() == 'observed'
    assert deployed['success'] is False
    assert any(step['id'] == 'failed-required' and step['exit_code'] == 11
               for step in deployed['steps'])
    assert receiver.runtime.current_task() is None


def test_foreign_existing_task_material_is_not_overwritten(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    registered_note(tools)
    saved = save(tools, payload)
    _, receiver = target(project, tmp_path / 'destination')
    foreign = (receiver.runtime.state / receiver.runtime.paths['standalone_tasks'] /
               'T1' / 'notes' / 'cache')
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'Foreign destination bytes\n')
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is False
    assert foreign.read_bytes() == b'Foreign destination bytes\n'
    assert receiver.runtime.task_queries.record('T1') is None


def test_changed_archive_digest_is_rejected_before_task_publication(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    package = Path(saved['package_path'])
    package.write_bytes(package.read_bytes() + b'tampered')
    _, receiver = target(project, tmp_path / 'destination')
    with pytest.raises(PoiseError, match='digest|integrity'):
        restore(receiver, saved['package_path'], saved['package_digest'])
    assert receiver.runtime.task_queries.record('T1') is None
    assert receiver.runtime.current_task() is None


def test_packaging_roots_cannot_overlap_the_task_material_source(
    project, recovery_tool,
):
    configure(project, recovery_tool)
    TransferPolicy.parse(project['cfg']['runtime_services']['transfer'])
    project['cfg']['runtime_services']['transfer']['directory'] = 'standalone/T1/archive'
    write_json(project['config_path'], project['cfg'])
    with pytest.raises(PoiseError, match='overlap'):
        WorkPoise(project['config_path'], 'overlapping-backup')


def test_occupied_destination_and_foreign_claim_are_preserved(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    _, receiver = target(project, tmp_path / 'destination')
    other = deepcopy(project['task'])
    other['id'] = 'OTHER'
    receiver.invoke(request('bootstrap', {
        'task': other, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    original = receiver.runtime.task_queries.record('OTHER')
    claim = receiver.runtime.ownership.snapshot(receiver.runtime.session)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    assert receiver.runtime.task_queries.record('OTHER') == original
    assert receiver.runtime.ownership.snapshot(receiver.runtime.session) == claim
    # No second Task claim may be acquired as an import side effect.
    assert receiver.runtime.task_queries.record('T1')['claimed_by'] is None


def test_unknown_check_outcome_is_not_repeated_or_converted_to_a_release(
    project, recovery_tool, monkeypatch,
):
    from runtime_services.test_uncertain_check_transfer import _pending
    configure(project, recovery_tool)
    producer, _, pending, calls = _pending(project, monkeypatch)
    before = producer.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='owned|handoff|[Uu]nknown|pending'):
        export(producer, ids=['T1'], request_id='unknown-outcome')
    assert producer.runtime.task_queries.record('T1') == before
    assert producer.runtime.current_task()['pending'] == pending
    assert calls == [pending['runs'][0]['run_id']]


def test_create_and_deploy_invoke_the_real_configured_tool(
    project, recovery_tool, tmp_path,
):
    log = tmp_path / 'actual-tool-calls.jsonl'
    proxy = Path(__file__).parent / 'fixtures' / 'record_tool_invocation.py'
    observed_tool = [sys.executable, str(proxy), str(log), *recovery_tool]
    _, tools, _, payload = prepared(
        project, recovery_tool, recovery_changes={'tool_argv': observed_tool},
    )
    saved = save(tools, payload)
    _, receiver = target(project, tmp_path / 'destination')
    assert restore(receiver, saved['package_path'], saved['package_digest'])['success'] is True
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    manifests = []
    for call in calls:
        if 'flow' in call and 'run' in call and '--manifest' in call:
            manifests.append(json.loads(Path(call[call.index('--manifest') + 1]).read_text()))
    operations = [step['op'] for manifest in manifests for step in manifest['steps']]
    assert 'archive' in operations
    assert 'extract' in operations
    restore_flow = next(manifest for manifest in manifests
                        if any(step['op'] == 'extract' for step in manifest['steps']))
    assert [step['id'] for step in restore_flow['steps']] == ['extract', 'place', 'components', 'validate']
    for call in calls:
        if '--session' in call:
            session = Path(call[call.index('--session') + 1])
            assert not session.is_relative_to(Path(receiver.runtime.state) / 'standalone' / 'T1')


def test_unavailable_source_commit_fails_without_guessing_or_touching_other_code(
    project, recovery_tool, tmp_path,
):
    from .compact_helpers import tiny_repository
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    dst, _ = target(project, tmp_path / 'destination')
    unrelated = tmp_path / 'unrelated-source'
    tiny_repository(unrelated)
    before = git(unrelated, 'show-ref')
    dst['cfg']['runtime_services']['transfer']['recovery']['repositories']['task'] = str(unrelated)
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is False
    assert deployed['status'] == 'failed'
    assert deployed['reason'] == 'source_unavailable'
    assert receiver.runtime.task_queries.record('T1') is None
    assert git(unrelated, 'show-ref') == before
    assert (unrelated / 'version').read_text() == 'installed current system\n'


def test_explicit_rewind_rechecks_current_stages_without_rolling_back_systems(
    project, recovery_tool,
):
    from runtime_services.restart_auto_support import launch, prepared as replay_prepared
    configure(project, recovery_tool)
    system = Path(project['cfg']['runtime_services']['transfer']['recovery'][
        'systems']['poise']['repository'])
    case = replay_prepared(project)
    historical_commit = git(system, 'rev-parse', 'HEAD')
    (system / 'version').write_text('New currently installed version\n')
    git(system, 'add', '.')
    git(system, 'commit', '-m', 'Current system differs from earlier proof')
    installed_head = git(system, 'rev-parse', 'HEAD')
    assert installed_head != historical_commit
    before = {item['id'] for item in case['client'].runtime.evidence_commands.list_for('T1')}
    response = launch(case, target='code_review')
    assert response['status'] == 'progression_target_reached'
    assert [item['stage'] for item in response['replay']['passed']] == [
        'tests', 'test_review', 'implementation',
    ]
    # The project under work is a system too. Fresh verification must inspect
    # the current checkpoint code, not silently check out historical proofs.
    assert git(case['root'], 'rev-parse', 'HEAD') == case['saved']
    assert case['log'].read_text().splitlines() == [case['saved']] * 3
    assert git(system, 'rev-parse', 'HEAD') == installed_head
    assert (system / 'version').read_text() == 'New currently installed version\n'
    after = case['client'].runtime.evidence_commands.list_for('T1')
    new_receipts = [item for item in after if item['id'] not in before]
    assert len(new_receipts) == 3
    assert all(item['commit'] == case['saved'] for item in new_receipts)
    assert all(item['systems']['poise']['commit'] == installed_head for item in new_receipts)
