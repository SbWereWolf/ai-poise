"""Accepted negative boundaries, using disposable data and real public owners."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError, digest, file_digest

from .compact_helpers import prepared, registered_note, save, target
from .helpers import cli_environment, restore, pick
from .test_compact_recovery import archive_contents


def altered_package(saved, path, change):
    """Corrupt only a disposable transfer fixture, not registered live artifacts."""
    entries, manifest = archive_contents(saved)
    extras = change(entries, manifest) or []
    for item in manifest['files']:
        if item['path'] in entries:
            item['size'] = len(entries[item['path']])
            item['digest'] = hashlib.sha256(entries[item['path']]).hexdigest()
    entries['manifest.json'] = json.dumps(manifest).encode()
    with tarfile.open(path, 'w:gz') as archive:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for member in extras:
            archive.addfile(member)
    return path, file_digest(path)


def selected_rows(runtime):
    """Independent persisted-state oracle; explicit columns, disposable DB only."""
    with runtime.store.transaction() as database:
        return {
            'tasks': [tuple(row) for row in database.execute(
                'SELECT id,status,claimed_by,stage_index,iteration,version,current_submission_id FROM tasks ORDER BY id')],
            'results': [tuple(row) for row in database.execute(
                'SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id')],
            'evidence': [tuple(row) for row in database.execute(
                'SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id')],
            'events': [tuple(row) for row in database.execute(
                'SELECT task_id,version,data FROM task_events ORDER BY seq')],
        }


def observe_stage_effects(monkeypatch):
    """Wrap existing entries, forwarding unchanged; never fake their answers."""
    from poise.application.replay import ReplayCoordinator
    calls = []
    for owner, name in [(WorkPoise, 'bootstrap'), (WorkPoise, 'verify'),
                        (WorkPoise, 'advance'), (ReplayCoordinator, 'run')]:
        original = getattr(owner, name)
        def observed(self, *args, _original=original, _name=name, **kwargs):
            calls.append(_name)
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(owner, name, observed)
    return calls


def instrument_stage_commands(project, counter):
    for method in project['task']['methods']:
        method['environment']['RECOVERY_STAGE_COUNTER'] = str(counter)


def install_stage_observer(context):
    source = Path(context['worktree']) / 'tests' / 'test_double.py'
    observed = Path(__file__).parent / 'fixtures' / 'observe_stage_command.py'
    source.write_bytes(source.read_bytes() + observed.read_bytes())


def test_inapplicable_diff_reports_failure_without_partial_task(project, recovery_tool, tmp_path):
    runtime, tools, context, payload = prepared(project, recovery_tool)
    tree = Path(context['worktree'])
    (tree / 'tests' / 'new.txt').write_text('Unsaved addition\n')
    saved = save(tools, payload)
    def damage(entries, manifest):
        entries[manifest['workspaces']['T1']['diff']] = (
            b'diff --git a/absent b/absent\n--- a/absent\n+++ b/absent\n'
            b'@@ -1 +1 @@\n-cannot match\n+replacement\n')
    path, sha = altered_package(saved, tmp_path / 'inapplicable.tar.gz', damage)
    _, receiver = target(project, tmp_path / 'destination')
    before = selected_rows(receiver.runtime)
    deployed = restore(receiver, path, sha)
    assert deployed['success'] is False
    assert deployed['reason'] == 'diff_inapplicable'
    assert selected_rows(receiver.runtime) == before
    assert (tree / 'tests' / 'new.txt').read_text() == 'Unsaved addition\n'
    assert runtime.current_task() is None


@pytest.mark.parametrize('damage', ['traversal', 'symlink'])
def test_unsafe_placement_preserves_outside_bytes_and_state(project, recovery_tool, tmp_path, damage):
    _, tools, _, payload = prepared(project, recovery_tool)
    registered_note(tools)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    outside = dst['root'] / 'protected'
    outside.mkdir()
    (outside / 'cache').write_bytes(b'Foreign bytes\n')
    recovery = dst['cfg']['runtime_services']['transfer']['recovery']
    if damage == 'traversal':
        recovery['mapping']['materials'][0]['destination'] = '../../../protected/cache'
    else:
        task_root = receiver.runtime.state / receiver.runtime.paths['standalone_tasks'] / 'T1'
        task_root.mkdir(parents=True)
        (task_root / 'notes').symlink_to(outside, target_is_directory=True)
    before = selected_rows(receiver.runtime)
    if damage == 'traversal':
        write_json(dst['config_path'], dst['cfg'])
    refusal = (r'Путь выходит из root: notes/cache' if damage == 'symlink'
               else 'path|escape|symlink|unsafe|relative')
    with pytest.raises(PoiseError, match=refusal):
        fresh = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
        restore(fresh, saved['package_path'], saved['package_digest'])
    assert (outside / 'cache').read_bytes() == b'Foreign bytes\n'
    assert selected_rows(receiver.runtime) == before
    assert receiver.runtime.current_task() is None


@pytest.mark.parametrize('damage', ['missing-config', 'incomplete-system'])
def test_incomplete_historical_proof_is_never_declared_complete(project, recovery_tool, tmp_path, damage):
    _, tools, _, payload = prepared(project, recovery_tool)
    checked = verify(tools, payload)
    saved = save(tools)
    def remove(entries, manifest):
        proof = next(item for item in manifest['proofs'] if item['receipt_id'] == checked['checks'][0]['id'])
        if damage == 'missing-config':
            del entries[proof['systems']['poise']['configs'][0]['archive_path']]
        else:
            del proof['systems']['project']
    path, sha = altered_package(saved, tmp_path / 'incomplete.tar.gz', remove)
    _, receiver = target(project, tmp_path / 'destination')
    before = selected_rows(receiver.runtime)
    with pytest.raises(PoiseError, match='config|proof|missing|incomplete|inventory'):
        restore(receiver, path, sha)
    assert selected_rows(receiver.runtime) == before
    assert receiver.runtime.current_task() is None


@pytest.mark.parametrize('condition', ['missing', 'incompatible', 'compatible'])
def test_unavailable_task_component_never_changes_shared_environment(project, recovery_tool, tmp_path, condition):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    shared = tmp_path / 'shared-package'
    if condition != 'missing':
        shared.write_bytes(b'Compatible task component\n' if condition == 'compatible'
                           else b'Installed incompatible component\n')
    script = Path(__file__).parent / 'fixtures' / 'require_component.py'
    steps = [{'id': 'require-component', 'op': 'verification',
              'argv': [sys.executable, str(script), str(shared)], 'onError': 'stop'}]
    _, receiver = target(project, tmp_path / 'destination', components=steps)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is (condition == 'compatible')
    expected_exit = 0 if condition == 'compatible' else 13
    assert any(item['id'] == 'require-component' and item['exit_code'] == expected_exit for item in deployed['steps'])
    assert shared.exists() is (condition != 'missing')
    if shared.exists():
        assert shared.read_bytes() == (b'Compatible task component\n' if condition == 'compatible'
                                       else b'Installed incompatible component\n')
    assert receiver.runtime.current_task() is None


@pytest.mark.parametrize('failing_step', ['extract', 'place', 'configure', 'validate'])
def test_each_delivery_failure_is_truthful_and_has_zero_stage_effects(project, recovery_tool, tmp_path, monkeypatch, failing_step):
    stage_counter = tmp_path / 'stage-commands'
    instrument_stage_commands(project, stage_counter)
    _, tools, context, payload = prepared(project, recovery_tool)
    install_stage_observer(context)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    pipeline = dst['cfg']['runtime_services']['transfer']['recovery']['restore_flow']['steps']
    if failing_step == 'configure':
        pipeline.insert(-1, {'id': 'configure', 'op': 'copy',
            'from': str(tmp_path / 'missing-task-config.env'),
            'to': '${variables.task_root}/task.env', 'onError': 'stop'})
    index = next(i for i, step in enumerate(pipeline) if step['id'] == failing_step)
    marker = tmp_path / 'must-not-follow'
    if failing_step == 'extract':
        pipeline[index]['from'] = str(tmp_path / 'unavailable-archive')
    elif failing_step == 'place':
        # Real configured placement fails because a source is unavailable.
        dst['cfg']['runtime_services']['transfer']['recovery']['repositories']['task'] = str(tmp_path / 'unavailable-repository')
    elif failing_step == 'configure':
        pass
    else:
        # Corrupt the placed Task through a declared operation before the real
        # final validator; never replace the validator with a synthetic exit.
        pipeline.insert(index, {'id': 'invalidate', 'op': 'command',
            'argv': [sys.executable, str(Path(__file__).parent / 'fixtures' / 'invalidate_task.py'),
                     '${variables.staging}/snapshot.sqlite'], 'onError': 'stop'})
        index += 1
    pipeline.insert(index + 1, {'id': 'following', 'op': 'write', 'to': str(marker), 'content': 'incorrect'})
    for step in pipeline:
        step['onError'] = 'stop'
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    calls = observe_stage_effects(monkeypatch)
    before = selected_rows(receiver.runtime)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is False
    assert deployed['status'] == 'failed'
    assert any(step['id'] == failing_step and step['status'] == 'failed' for step in deployed['steps'])
    assert calls == []
    assert not stage_counter.exists()
    assert selected_rows(receiver.runtime) == before
    assert not marker.exists()
    assert receiver.runtime.current_task() is None


def test_successful_delivery_has_zero_stage_commands_or_transitions(project, recovery_tool, tmp_path, monkeypatch):
    stage_counter = tmp_path / 'stage-commands'
    instrument_stage_commands(project, stage_counter)
    runtime, tools, context, payload = prepared(project, recovery_tool)
    install_stage_observer(context)
    saved = save(tools, payload)
    original = runtime.task_queries.record('T1')
    _, receiver = target(project, tmp_path / 'destination')
    calls = observe_stage_effects(monkeypatch)
    deployed = restore(receiver, saved['package_path'], saved['package_digest'])
    assert deployed['success'] is True
    assert calls == []
    assert not stage_counter.exists()
    imported = receiver.runtime.task_queries.record('T1')
    assert (imported['stage_index'], imported['iteration'], imported['status'], imported['pending']) == (
        original['stage_index'], original['iteration'], original['status'], original['pending'])
    assert receiver.runtime.evidence_commands.list_for('T1') == []
    assert receiver.runtime.current_task() is None


@pytest.mark.parametrize('damage', ['missing-artifact', 'corrupt-artifact', 'missing-receipt', 'corrupt-receipt'])
def test_current_verification_admission_rejects_damaged_mandatory_inputs(project, recovery_tool, tmp_path, damage):
    _, tools, _, payload = prepared(project, recovery_tool)
    note = registered_note(tools)
    payload['artifact_paths'] = [str(note)]
    verify(tools, payload)
    saved = save(tools)
    _, receiver = target(project, tmp_path / 'destination')
    assert restore(receiver, saved['package_path'], saved['package_digest'])['success'] is True
    context = pick(receiver, 'T1')
    if 'artifact' in damage:
        damaged = Path(context['result_template']['artifact_paths'][0])
    else:
        fresh = verify(receiver, result(context, 'Fresh proof before damage'))
        damaged = Path(fresh['checks'][0]['stdout'])
    if damage.startswith('missing'):
        damaged.unlink()
    else:
        damaged.write_bytes(b'Corrupt mandatory current input\n')
    before = selected_rows(receiver.runtime)
    refusal = {
        'missing-artifact': 'Артефакт не существует:',
        'corrupt-artifact': 'Артефакт изменён после регистрации:',
    }.get(damage, 'artifact|evidence|receipt|digest|missing|changed|disappeared')
    with pytest.raises(PoiseError, match=refusal):
        if 'artifact' in damage:
            verify(receiver, result(context, 'Cannot admit damaged material'))
        else:
            verify(receiver, None)
    assert selected_rows(receiver.runtime) == before


def test_fresh_failed_current_check_cannot_reuse_historical_pass(project, recovery_tool, tmp_path):
    _, tools, context, payload = prepared(project, recovery_tool)
    old = verify(tools, payload)
    assert old['checks'][0]['passed'] is True
    saved = save(tools)
    _, receiver = target(project, tmp_path / 'destination')
    assert restore(receiver, saved['package_path'], saved['package_digest'])['success'] is True
    current = pick(receiver, 'T1')
    # Allowed test-owned WIP deliberately changes the current check result.
    test = Path(current['worktree']) / 'tests' / 'test_double.py'
    test.write_bytes((Path(__file__).parent / 'fixtures' / 'failing_current_test.py').read_bytes())
    fresh = verify(receiver, result(current, 'Current result differs'))
    assert fresh['status'] != 'verified'
    assert any(not receipt['passed'] for receipt in fresh['checks'])
    assert {r['id'] for r in fresh['checks']}.isdisjoint(r['id'] for r in old['checks'])
    before = selected_rows(receiver.runtime)
    stopped = receiver.invoke(request('advance', {'task_id': 'T1', 'request_id': 'denied-old-pass', 'target_stage': 'test_review'}))
    assert stopped['status'] == 'progression_work_required'
    assert selected_rows(receiver.runtime) == before
    actual = receiver.runtime.task_queries.record('T1')
    assert actual['stage_index'] == 0
    assert actual['status'] == 'active'


def test_existing_same_task_collision_preserves_selected_database_fields(project, recovery_tool, tmp_path):
    runtime, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    source = selected_rows(runtime)
    dst, receiver = target(project, tmp_path / 'destination')
    bootstrap(receiver, dst)
    before = selected_rows(receiver.runtime)
    claim = receiver.runtime.ownership.snapshot(receiver.runtime.session)
    with pytest.raises(PoiseError, match='exist|collision|conflict'):
        restore(receiver, saved['package_path'], saved['package_digest'])
    assert selected_rows(receiver.runtime) == before
    assert selected_rows(runtime) == source
    assert receiver.runtime.ownership.snapshot(receiver.runtime.session) == claim


def test_import_replay_after_further_progress_preserves_state_and_effect_count(project, recovery_tool, tmp_path):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    counter = tmp_path / 'component-count'
    script = Path(__file__).parent / 'fixtures' / 'task_component.py'
    steps = [{'id': 'one-component', 'op': 'command',
              'argv': [sys.executable, str(script), '${variables.task_root}', str(counter)], 'onError': 'stop'}]
    _, receiver = target(project, tmp_path / 'destination', components=steps)
    first = restore(receiver, saved['package_path'], saved['package_digest'])
    assert first['success'] is True
    current = pick(receiver, 'T1')
    verify(receiver, result(current, 'Progress after deployment'))
    advanced = receiver.invoke(request('advance', {'task_id': 'T1', 'request_id': 'progress', 'target_stage': 'test_review'}))
    assert advanced['stage'] == 'test_review'
    before = selected_rows(receiver.runtime)
    second = restore(receiver, saved['package_path'], saved['package_digest'])
    assert second['replayed'] is True
    assert selected_rows(receiver.runtime) == before
    assert counter.read_text() == 'called\n'


def test_unknown_restore_step_is_persisted_and_never_repeated(project, recovery_tool, tmp_path):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    counter = tmp_path / 'unknown-step-count'
    # The real command writes an effect, then kills its flow runner before a
    # terminal step receipt. No forged Poise pending row or parser bypass.
    script = Path(__file__).parent / 'fixtures' / 'interrupt_flow.py'
    dst, receiver = target(project, tmp_path / 'destination')
    pipeline = dst['cfg']['runtime_services']['transfer']['recovery']['restore_flow']['steps']
    pipeline.insert(0, {'id': 'uncertain-effect', 'op': 'command',
        'argv': [sys.executable, str(script), str(counter)], 'onError': 'stop'})
    write_json(dst['config_path'], dst['cfg'])
    receiver = WorkTools(WorkPoise(dst['config_path'], 'receiver'))
    packet = {'action': 'import', 'request_id': 'unknown-restore',
              'package_path': saved['package_path'], 'package_digest': saved['package_digest']}
    first = receiver.invoke(request('transfer', packet))
    assert first['success'] is False
    assert first['status'] == 'unknown'
    persisted = receiver.runtime.transfer_tools.port.repo.request(
        receiver.runtime.session, packet['request_id'], digest(packet))
    assert persisted['phase'] == 'unknown'
    before = selected_rows(receiver.runtime)
    again = receiver.invoke(request('transfer', packet))
    assert again['success'] is False
    assert again['status'] == 'unknown'
    assert counter.read_text() == 'effect\n'
    assert selected_rows(receiver.runtime) == before
    assert receiver.runtime.current_task() is None


def test_real_cli_consumes_compact_transfer_and_reports_valid_deployment(project, recovery_tool, tmp_path):
    _, tools, _, payload = prepared(project, recovery_tool)
    saved = save(tools, payload)
    dst, receiver = target(project, tmp_path / 'destination')
    environment = cli_environment(dst, 'fixture-cli.caller.json')
    packet = request('transfer', {'action': 'import', 'request_id': 'cli-compact-import',
        'package_path': saved['package_path'], 'package_digest': saved['package_digest']})
    process = subprocess.run([sys.executable, '-B', '-m', 'poise', 'work'],
        input=json.dumps(packet), text=True, capture_output=True, env=environment)
    assert process.returncode == 0, process.stdout + process.stderr
    output = json.loads(process.stdout)
    if output.get('details') == 'full_result':
        output = json.loads(Path(output['response_path']).read_text())
    assert output['success'] is True
    assert output['status'] == 'imported'
    imported = receiver.runtime.task_queries.record('T1')
    assert imported['id'] == 'T1'
    assert imported['claimed_by'] is None
    assert imported['pending'] is None
    assert imported['stage_index'] == 0
