"""Ordinary participant changes preserve real work without submitting or copying it."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import bootstrap, configure, request, result, text_artifact, verify
from conftest import WorkPoise, add_test, git
from poise.application.work import WorkTools
from poise.common import PoiseError


def started(project):
    configure(project)
    owner = WorkTools(WorkPoise(project['config_path'], 'owner'))
    return owner, bootstrap(owner, project)


def release_packet(owner, request_id='continue-in-place'):
    current = owner.runtime.current_task()
    return request('release', {
        'request_id': request_id, 'task_id': current['id'],
        'expected_version': current['_version'], 'reason': 'Continue the same Task in its existing working area.',
    })


def proof_rows(owner):
    with owner.runtime.store.transaction() as db:
        return {
            'submissions': [tuple(row) for row in db.execute("SELECT * FROM submissions WHERE task_id='T1' ORDER BY seq")],
            'results': [tuple(row) for row in db.execute("SELECT * FROM task_results WHERE task_id='T1' ORDER BY submission_id")],
            'evidence': [tuple(row) for row in db.execute("SELECT * FROM evidence WHERE task_id='T1' ORDER BY id")],
        }


def test_unverified_work_is_continued_without_commit_or_implicit_submission(project):
    owner, context = started(project)
    root = Path(context['worktree'])
    note = root / 'src/unfinished.txt'
    note.write_bytes(b'continue these exact unfinished bytes\n')
    head = git(root, 'rev-parse', 'HEAD')
    status = git(root, 'status', '--porcelain=v1')
    before = deepcopy(owner.runtime.current_task())
    proof = proof_rows(owner)

    assert owner.invoke(release_packet(owner))['status'] == 'ownership_released'
    receiver = WorkTools(WorkPoise(project['config_path'], 'next-participant'))
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))

    assert resumed['status'] == 'active'
    assert resumed['stage'] == context['stage']
    assert resumed['iteration'] == context['iteration']
    assert resumed['worktree'] == str(root)
    assert git(root, 'rev-parse', 'HEAD') == head
    assert git(root, 'status', '--porcelain=v1') == status
    assert note.read_bytes() == b'continue these exact unfinished bytes\n'
    assert proof_rows(owner) == proof
    assert receiver.runtime.current_task()['last_report'] == before['last_report']


def test_verified_result_keeps_exact_existing_proof_without_executing_again(project):
    owner, context = started(project)
    add_test(context['worktree'])
    assert verify(owner, result(context))['status'] == 'verified'
    root = Path(context['worktree'])
    head = git(root, 'rev-parse', 'HEAD')
    proof = proof_rows(owner)
    assert owner.invoke(release_packet(owner))['status'] == 'ownership_released'
    receiver = WorkTools(WorkPoise(project['config_path'], 'next-participant'))
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert resumed['status'] == 'verified'
    assert proof_rows(owner) == proof
    assert git(root, 'rev-parse', 'HEAD') == head


def test_old_release_replay_preserves_a_different_current_task(project):
    owner, _ = started(project)
    packet = release_packet(owner)
    assert owner.invoke(packet)['status'] == 'ownership_released'
    owner.invoke(request('task', {
        'action': 'create', 'request_id': 'create-other-task', 'task_id': 'OTHER', 'sprint_id': None,
    }))
    before = deepcopy(owner.runtime.current_task())
    ownership = owner.runtime.ownership.snapshot('owner')
    assert before['id'] == 'OTHER'

    replay = owner.invoke(packet)

    assert replay['status'] == 'ownership_released' and replay['replayed'] is True
    assert owner.runtime.current_task() == before
    assert owner.runtime.ownership.snapshot('owner') == ownership


def test_changed_release_intent_is_refused_without_releasing_another_task(project):
    owner, _ = started(project)
    packet = release_packet(owner)
    assert owner.invoke(packet)['status'] == 'ownership_released'
    owner.invoke(request('task', {
        'action': 'create', 'request_id': 'create-other-task', 'task_id': 'OTHER', 'sprint_id': None,
    }))
    before = deepcopy(owner.runtime.current_task())
    changed = deepcopy(packet)
    changed['input']['reason'] = 'A different explicit intent.'
    with pytest.raises(PoiseError, match='Ordinary release caller or request conflict'):
        owner.invoke(changed)
    assert owner.runtime.current_task() == before


def test_meaningful_working_note_is_created_in_the_task_area_before_participant_change(project):
    owner, context = started(project)
    created = owner.invoke(request('artifacts', {'items': [
        text_artifact('task', 'working-note.md', 'Continue this unfinished analysis.\n'),
    ]}))
    note = Path(created['artifact_paths'][0])
    assert note.is_relative_to(Path(context['task_root']))
    assert owner.invoke(release_packet(owner))['status'] == 'ownership_released'
    receiver = WorkTools(WorkPoise(project['config_path'], 'next-participant'))
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert resumed['worktree'] == context['worktree']
    assert note.read_bytes() == b'Continue this unfinished analysis.\n'
