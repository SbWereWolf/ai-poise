"""Permanent regressions for independently confirmed recovery findings.

The existing public fixture/tool boundary is the subject. Expected material
bytes, effects, inventory and stage position are test-owned contracts.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError

from .compact_helpers import prepared, registered_note, save, target
from .helpers import export, pick, restore
from .test_compact_denials import (
    install_stage_observer, instrument_stage_commands, observe_stage_effects, selected_rows,
)
from .test_compact_recovery import archive_contents, assert_compact_inventory


FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.mark.parametrize('role', ['task-material', 'configuration'])
@pytest.mark.parametrize('changed_version', [False, True])
def test_declared_configuration_role_is_material_not_proof(
    project, recovery_tool, tmp_path, role, changed_version,
):
    mapping = json.loads((FIXTURES / 'compact-recovery-policy.json').read_text())['mapping']
    mapping['materials'][0]['role'] = role
    runtime, tools, context, payload = prepared(
        project, recovery_tool, recovery_changes={'mapping': mapping})
    note = registered_note(tools)
    source_head = git(context['worktree'], 'rev-parse', 'HEAD')
    saved = save(tools, payload)
    entries, manifest = archive_contents(saved)
    assert entries['materials/T1/notes/cache'] == b'Necessary task material\n'
    material = next(item for item in manifest['files'] if item.get('material_id') == 'note')
    assert material['role'] == role
    assert material['task_id'] == 'T1'
    assert 'configuration_destination' not in material
    expected_path = 'notes/cache'
    receiving_mapping = deepcopy(mapping)
    if changed_version:
        receiving_mapping['version'] = 'fixture-v2'
        receiving_mapping['materials'][0]['destination'] = 'documents/current-note'
        expected_path = 'documents/current-note'
    dst, receiver = target(project, tmp_path / 'destination', mapping=receiving_mapping)
    if changed_version:
        waiting = restore(receiver, saved['package_path'], saved['package_digest'])
        assert waiting['status'] == 'placement_decision_required'
        assert receiver.runtime.task_queries.record('T1') is None
        decision = json.loads((FIXTURES / 'configuration-role-placement.json').read_text())
        dst['cfg']['runtime_services']['transfer']['recovery']['placement_decisions'] = [decision]
        write_json(dst['config_path'], dst['cfg'])
        receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    try:
        deployed = restore(receiver, saved['package_path'], saved['package_digest'],
                           request_id='role-deployment')
    except KeyError as error:
        if error.args != ('configuration_destination',):
            raise
        pytest.fail('Mapped configuration material dispatched as proof companion')
    assert deployed['success'] is True
    restored_root = Path(deployed['delivery']['binding']['locations'][context['task_root']])
    assert (restored_root / expected_path).read_bytes() == b'Necessary task material\n'
    with receiver.runtime.store.transaction() as database:
        notes = database.execute('SELECT owner,scope,path FROM artifacts WHERE path=?',
                                 (str(restored_root / expected_path),)).fetchall()
    assert [tuple(row) for row in notes] == [('T1', 'task', str(restored_root / expected_path))]
    assert note.read_bytes() == b'Necessary task material\n'
    assert git(context['worktree'], 'rev-parse', 'HEAD') == source_head
    assert runtime.current_task() is None
    before = selected_rows(receiver.runtime)
    replay = restore(receiver, saved['package_path'], saved['package_digest'],
                     request_id='role-deployment')
    assert replay['success'] is True and replay['replayed'] is True
    assert selected_rows(receiver.runtime) == before


def child_import(dst, packet):
    environment = dict(os.environ)
    checkout = Path(__file__).resolve().parents[2]
    environment['PYTHONPATH'] = os.pathsep.join([str(checkout / 'tests'), str(checkout / 'src')])
    return subprocess.run(
        [sys.executable, '-B', str(FIXTURES / 'import_in_child.py'), str(dst['config_path'])],
        input=json.dumps(packet), text=True, capture_output=True, env=environment,
    )


def test_coordinator_crash_before_tool_state_never_repeats_effect(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    counter = tmp_path / 'effect-count'
    dst['cfg']['runtime_services']['transfer']['recovery']['tool_argv'] = [
        sys.executable, str(FIXTURES / 'interrupt_flow.py'), str(counter)]
    write_json(dst['config_path'], dst['cfg'])
    packet = request('transfer', {'action': 'import', 'request_id': 'before-state-crash',
        'package_path': saved['package_path'], 'package_digest': saved['package_digest']})
    first = child_import(dst, packet)
    assert first.returncode == -signal.SIGKILL, first.stdout + first.stderr
    assert counter.read_text() == 'effect\n'
    assert receiver.runtime.task_queries.record('T1') is None
    assert not list((dst['root'] / 'state' / 'transfers').rglob('state.json'))
    before = selected_rows(receiver.runtime)
    for _ in range(2):
        replay = child_import(dst, packet)
        if replay.returncode == -signal.SIGKILL and counter.read_text() == 'effect\neffect\n':
            pytest.fail('Interrupted restore repeated its uncertain external effect')
        assert replay.returncode == 0, replay.stdout + replay.stderr
        result = json.loads(replay.stdout)
        assert result['success'] is False
        assert result['status'] == 'unknown'
        assert counter.read_text() == 'effect\n'
        assert selected_rows(receiver.runtime) == before
        with receiver.runtime.store.transaction() as database:
            rows = database.execute(
                'SELECT data FROM transfer_requests WHERE actor=? AND request_id=?',
                ('receiver', 'before-state-crash')).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]['data'])['phase'] == 'unknown'
    assert not list((dst['root'] / 'state' / 'transfers').rglob('state.json'))


def test_known_spawn_failure_replays_without_effect_or_task_publication(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    dst, _ = target(project, tmp_path / 'destination')
    dst['cfg']['runtime_services']['transfer']['recovery']['tool_argv'] = [
        str(tmp_path / 'explicitly-missing-executable')]
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    before = selected_rows(receiver.runtime)
    first = restore(receiver, saved['package_path'], saved['package_digest'])
    assert first['status'] == 'failed'
    assert first['success'] is False
    again = restore(receiver, saved['package_path'], saved['package_digest'])
    assert again['status'] == 'failed'
    assert again['success'] is False
    assert again['replayed'] is True
    assert selected_rows(receiver.runtime) == before
    assert receiver.runtime.task_queries.record('T1') is None


def test_interrupted_request_rejects_changed_executable_without_second_effect(
    project, recovery_tool, tmp_path,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    counter = tmp_path / 'effect-count'
    recovery = dst['cfg']['runtime_services']['transfer']['recovery']
    recovery['tool_argv'] = [sys.executable, str(FIXTURES / 'interrupt_flow.py'), str(counter)]
    write_json(dst['config_path'], dst['cfg'])
    packet = request('transfer', {'action': 'import', 'request_id': 'changed-executable',
        'package_path': saved['package_path'], 'package_digest': saved['package_digest']})
    first = child_import(dst, packet)
    assert first.returncode == -signal.SIGKILL, first.stdout + first.stderr
    assert counter.read_text() == 'effect\n'
    before = selected_rows(receiver.runtime)
    recovery['tool_argv'] = list(recovery_tool)
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    with pytest.raises(PoiseError, match='changed|differs|identity'):
        receiver.invoke(packet)
    assert counter.read_text() == 'effect\n'
    assert selected_rows(receiver.runtime) == before
    assert receiver.runtime.task_queries.record('T1') is None


def assert_restored_code(tree, commit, base_tree, expected):
    """Inspect actual Git/files, independently of package metadata and diff bytes."""
    assert git(tree, 'rev-parse', 'HEAD') == commit
    assert git(tree, 'rev-parse', 'HEAD^{tree}') == base_tree
    assert git(tree, 'symbolic-ref', 'HEAD') == 'refs/heads/tasks/T1'
    assert git(tree, 'rev-parse', 'refs/heads/tasks/T1') == commit
    inventory = git(tree, 'ls-files', '--cached', '--others', '--exclude-standard')
    assert sorted(inventory.splitlines()) == sorted(expected)
    for name, content in expected.items():
        assert (tree / name).is_file(), name
        assert (tree / name).read_bytes() == content.encode(), name
    assert git(tree, 'diff', '--name-only', 'HEAD') == 'tests/tracked.txt'
    assert sorted(git(tree, 'ls-files', '--others', '--exclude-standard').splitlines()) == [
        'tests/test_double.py', 'tests/untracked.txt']


@pytest.mark.parametrize('damage', [
    'missing-tracked', 'wrong-tracked', 'missing-untracked', 'wrong-untracked',
    'extra-file', 'wrong-ref', 'wrong-commit',
])
def test_restored_code_oracle_rejects_lost_or_wrong_worktree(project, tmp_path, damage):
    """Assertion sensitivity only; this does not claim a product restore occurred."""
    expected = json.loads((FIXTURES / 'reexport-source-code.json').read_text())
    baseline = project['app'] / 'tests'
    baseline.mkdir()
    (baseline / 'tracked.txt').write_text('Before text change\n')
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Independent code oracle baseline')
    tree = tmp_path / 'oracle-worktree'
    git(project['app'], 'clone', '--no-hardlinks', str(project['app']), str(tree))
    git(tree, 'checkout', '-b', 'tasks/T1')
    commit = git(tree, 'rev-parse', 'HEAD')
    base_tree = git(tree, 'rev-parse', 'HEAD^{tree}')
    for name, content in expected.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(content)
    assert_restored_code(tree, commit, base_tree, expected)
    if damage.startswith(('missing-', 'wrong-')) and damage.endswith(('tracked', 'untracked')):
        path = tree / 'tests' / ('untracked.txt' if damage.endswith('-untracked') else 'tracked.txt')
        if damage.startswith('missing-'):
            path.unlink()
        else:
            path.write_bytes(b'Incorrect restored bytes\n')
    elif damage == 'extra-file':
        (tree / 'unexpected.txt').write_bytes(b'Undeclared source\n')
    elif damage == 'wrong-ref':
        git(tree, 'branch', '-m', 'wrong-ref')
    else:
        git(tree, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '--allow-empty', '-m', 'Wrong restored commit')
    with pytest.raises(AssertionError):
        assert_restored_code(tree, commit, base_tree, expected)


def test_recovered_task_can_be_exported_and_restored_again_without_original_source(
    project, recovery_tool, tmp_path, monkeypatch,
):
    stage_counter = tmp_path / 'forbidden-stage-command'
    instrument_stage_commands(project, stage_counter)
    expected_code = json.loads((FIXTURES / 'reexport-source-code.json').read_text())
    baseline = project['app'] / 'tests'
    baseline.mkdir()
    (baseline / 'tracked.txt').write_bytes(b'Before text change\n')
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Tracked two-hop code input')
    git(project['remote'], 'fetch', str(project['app']), 'main:main')
    runtime, tools, context, payload = prepared(project, recovery_tool)
    install_stage_observer(context)
    source_worktree = Path(context['worktree'])
    (source_worktree / 'tests/tracked.txt').write_bytes(b'Unsaved text change\n')
    (source_worktree / 'tests/untracked.txt').write_bytes(b'Unsaved addition\n')
    source_commit = git(source_worktree, 'rev-parse', 'HEAD')
    source_tree = git(source_worktree, 'rev-parse', 'HEAD^{tree}')
    assert_restored_code(source_worktree, source_commit, source_tree, expected_code)
    original_note = registered_note(tools)
    saved = save(tools, payload)
    first, _ = target(project, tmp_path / 'first')
    second, _ = target(project, tmp_path / 'second')
    for dst in (first, second):
        dst['cfg']['runtime_services']['transfer']['recovery']['repositories']['task'] = str(dst['app'])
        write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(first['config_path'], 'receiver'))
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    first_worktree = Path(receiver.runtime.task_queries.record('T1')['worktree'])
    assert_restored_code(first_worktree, source_commit, source_tree, expected_code)
    current_root = Path(deployed['delivery']['binding']['locations'][context['task_root']])
    assert (current_root / 'notes/cache').read_bytes() == b'Necessary task material\n'
    assert not (current_root / 'artifacts/cache').exists()
    _, manifest = archive_contents(saved)
    omitted = set(manifest['omitted_artifacts'])
    with receiver.runtime.store.transaction() as database:
        records = [tuple(row) for row in database.execute(
            'SELECT id,path,digest FROM artifacts ORDER BY id')]
    absent = {identifier for identifier, path, _ in records if not Path(path).exists()}
    assert absent and absent == omitted
    before = selected_rows(receiver.runtime)
    source_bytes = original_note.read_bytes()
    source_head = git(context['worktree'], 'rev-parse', 'HEAD')
    hidden = project['root'].with_name('unavailable-original-task-store')
    project['root'].rename(hidden)
    effects = observe_stage_effects(monkeypatch)
    try:
        next_saved = export(receiver, ids=['T1'], request_id='next-generation')
    except PoiseError as error:
        if str(error) != 'Registered artifact changed or disappeared before export':
            raise
        pytest.fail('Recovered task cannot export authenticated omitted history')
    assert next_saved['success'] is True
    assert_compact_inventory(next_saved, {
        'manifest.json', 'snapshot.sqlite', 'diffs/T1.patch', 'materials/T1/notes/cache'})
    entries, _ = archive_contents(next_saved)
    assert entries['materials/T1/notes/cache'] == b'Necessary task material\n'
    last = WorkTools(WorkPoise(second['config_path'], 'receiver'))
    restored = restore(last, next_saved['package_path'], next_saved['package_digest'])
    assert restored['success'] is True
    final_root = Path(restored['delivery']['binding']['locations'][str(current_root)])
    assert (final_root / 'notes/cache').read_bytes() == b'Necessary task material\n'
    assert not (final_root / 'artifacts/cache').exists()
    current = last.runtime.task_queries.record('T1')
    assert_restored_code(Path(current['worktree']), source_commit, source_tree, expected_code)
    assert (current['stage_index'], current['iteration'], current['status']) == (0, 1, 'active')
    assert effects == []
    assert not stage_counter.exists()
    assert selected_rows(receiver.runtime) == before
    assert (hidden / original_note.relative_to(project['root'])).read_bytes() == source_bytes
    assert_restored_code(hidden / source_worktree.relative_to(project['root']),
                         source_commit, source_tree, expected_code)
    # Source preservation uses the untouched Git owner, not an exported hash.
    assert git(project['app'], 'rev-parse', 'tasks/T1') == source_head
    with receiver.runtime.store.transaction() as database:
        assert [tuple(row) for row in database.execute(
            'SELECT id,path,digest FROM artifacts ORDER BY id')] == records
    assert all(not Path(path).exists() for identifier, path, _ in records if identifier in omitted)


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_required_material_is_not_excused_by_an_imported_historical_omission(
    project, recovery_tool, tmp_path, damage,
):
    _, tools, context, payload = prepared(project, recovery_tool)
    registered_note(tools)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    root = Path(deployed['delivery']['binding']['locations'][context['task_root']])
    note = root / 'notes/cache'
    # Prove that this state can be exported before damaging a required file.
    # Without this control the historical-omission bug could mask the denial.
    try:
        control = export(receiver, ids=['T1'], request_id='undamaged-next-generation')
    except PoiseError as error:
        if str(error) != 'Registered artifact changed or disappeared before export':
            raise
        pytest.fail('Recovered task cannot export authenticated omitted history')
    assert control['success'] is True
    if damage == 'missing':
        note.unlink()
    else:
        note.write_bytes(b'Changed current material\n')
    before = selected_rows(receiver.runtime)
    with pytest.raises(PoiseError, match='artifact|material|digest|changed|disappeared'):
        export(receiver, ids=['T1'], request_id='damaged-next-generation')
    assert selected_rows(receiver.runtime) == before
    if damage == 'missing':
        assert not note.exists()
    else:
        assert note.read_bytes() == b'Changed current material\n'


@pytest.mark.parametrize('damage', ['missing-receipt', 'changed-receipt',
                                   'missing-config', 'changed-config'])
def test_new_current_proof_is_not_excused_by_imported_omissions(
    project, recovery_tool, tmp_path, damage,
):
    _, tools, _, payload = prepared(project, recovery_tool)
    registered_note(tools)
    saved = save(tools, payload)
    _, receiver = target(project, tmp_path / 'destination')
    assert restore(receiver, saved['package_path'], saved['package_digest'])['success'] is True
    context = pick(receiver, 'T1')
    fresh = verify(receiver, result(context, 'New current proof before checkpoint damage'))
    assert fresh['status'] == 'verified'
    receipt = next(item for item in receiver.runtime.evidence_commands.list_for('T1')
                   if item['id'] == fresh['checks'][0]['id'])
    damaged = (Path(receipt['stdout']) if damage.endswith('receipt')
               else Path(receipt['systems']['poise']['configs'][0]['captured_path']))
    assert damaged.is_file()
    try:
        control = save(receiver, request_id='undamaged-current-proof')
    except PoiseError as error:
        if str(error) != 'Registered artifact changed or disappeared before export':
            raise
        pytest.fail('Recovered task cannot export authenticated omitted history')
    assert control['success'] is True
    if damage.startswith('missing'):
        damaged.unlink()
    else:
        damaged.write_bytes(b'Changed required proof\n')
    before = selected_rows(receiver.runtime)
    with pytest.raises(PoiseError, match='proof|config|receipt|evidence|digest|changed|missing|disappeared'):
        export(receiver, ids=['T1'], request_id='damaged-current-proof')
    assert selected_rows(receiver.runtime) == before
