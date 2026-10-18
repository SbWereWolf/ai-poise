"""Participants reuse retained acceptance inputs in the existing Task working area."""
from pathlib import Path

import pytest

from conftest import WorkPoise
from batch.helpers import request, result
from poise.application.work import WorkTools
from poise.common import PoiseError
from verification.retention_helpers import BYTES, assert_bundle, assert_preserved, invoke_required, manifest, verify_input


def release(tools):
    current = tools.runtime.current_task()
    return tools.invoke(request('release', {
        'request_id': 'retained-input-release', 'reason': 'Independent repeat verification',
        'task_id': current['id'], 'expected_version': current['version'],
    }))


def test_released_participant_uses_retained_bytes_after_source_removal(project, tmp_path):
    tools, context, first, source, marker = verify_input(project, tmp_path)
    original = assert_bundle(first, context, first['commit'])
    released = release(tools)
    assert released['status'] == 'ownership_released'
    assert_preserved(original, context)
    source.unlink()
    source.parent.rmdir()
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    resumed = invoke_required(receiver, 'bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    })
    assert resumed['status'] == 'verified'
    assert_preserved(original, context)
    repair = invoke_required(receiver, 'bootstrap', {
        'task': None, 'decision': 'rework',
        'feedback': 'Repeat the same consumer from retained inputs.',
        'rework_stage': 'implementation',
    })
    repeated = invoke_required(receiver, 'verify', {
        'result': result(repair, 'Repeated retained input check.'), 'artifacts': [],
    })
    assert repeated['status'] == 'verified'
    assert marker.read_text() == 'run\nrun\n'
    assert (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').read_bytes() == BYTES
    assert_preserved(original, context)
    current = assert_bundle(repeated, context, repeated['commit'])
    replayed = invoke_required(receiver, 'verify', {
        'result': result(repair, 'Repeated retained input check.'), 'artifacts': [],
    })
    assert replayed['replayed'] is True
    assert_preserved(original, context)
    assert_preserved(current, context)
    assert marker.read_text() == 'run\nrun\n'


@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_release_keeps_invalid_input_for_the_next_participants_guard(project, tmp_path, fault):
    tools, context, _, _, marker = verify_input(project, tmp_path)
    target = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    if fault == 'missing':
        target.unlink()
    else:
        target.write_bytes(b'changed')
    before = tools.runtime.task_queries.record('T1')
    assert release(tools)['status'] == 'ownership_released'
    after = tools.runtime.task_queries.record('T1')
    assert after['claimed_by'] is None
    assert after['status'] == before['status']
    assert after['worktree'] == before['worktree']
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    with pytest.raises(PoiseError, match='exist|missing|digest|измен'):
        receiver.invoke(request('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
    assert receiver.runtime.current_task() is None
    assert tools.runtime.task_queries.record('T1') == after
    assert marker.read_text() == 'run\n'


@pytest.mark.parametrize('member', ['input', 'proof', 'manifest'])
@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_retained_material_damage_refuses_receiver_claim_and_exact_repair_retries(
    project, tmp_path, member, fault,
):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    trusted = assert_bundle(done, context, done['commit'])
    path, value = manifest(done, context)
    target = (Path(value['inputs'][0]['path']) if member == 'input' else
              next(Path(e['path']) for e in value['evidence'] if e['kind'] == 'output')
              if member == 'proof' else path)
    assert release(tools)['status'] == 'ownership_released'
    before = tools.runtime.task_queries.record('T1')
    assert before['claimed_by'] is None
    if fault == 'missing':
        target.unlink()
    else:
        target.write_bytes(b'external damage')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    packet = {'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}
    with pytest.raises(PoiseError, match='exist|missing|digest|changed|измен|integrity'):
        receiver.invoke(request('bootstrap', packet))
    assert receiver.runtime.current_task() is None
    claims = receiver.runtime.ownership.snapshot('receiver')
    assert claims.task_id is None and claims.worktree_task_id is None
    assert tools.runtime.task_queries.record('T1') == before
    assert marker.read_text() == 'run\n'
    # Test-owned fault repair restores the exact trusted bytes; no registry rebinding.
    target.write_bytes(trusted[target])
    resumed = invoke_required(receiver, 'bootstrap', packet)
    assert resumed['status'] == 'verified'
    claims = receiver.runtime.ownership.snapshot('receiver')
    assert claims.task_id == 'T1' and claims.worktree_task_id == 'T1'
    assert_preserved(trusted, context)
    assert marker.read_text() == 'run\n'
