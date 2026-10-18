"""Current draft acquisition preserves ordinary releases, history and shared WIP."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import bootstrap, request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_draft_continuation import draft
from runtime_services.test_task_restart import restart


def select(tools, task_id):
    return tools.invoke(request('bootstrap', {
        'task': {'id': task_id}, 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))


def create(tools, task_id):
    return tools.invoke(request('task', {
        'action': 'create', 'request_id': 'create-' + task_id,
        'task_id': task_id, 'sprint_id': None,
    }))


def edit(tools, task_id, marker):
    current = tools.runtime.task_queries.record(task_id)
    return tools.invoke(request('task', {
        'action': 'edit', 'request_id': 'edit-' + marker,
        'task_id': task_id, 'expected_revision': current['revision'],
        'patch': {'definition_of_done': [marker]}, 'remove': [],
    }))


def release_rows(runtime, task_id):
    with runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            "SELECT session_id,data FROM journal WHERE task_id=? "
            "AND event='ownership.ordinary_released' ORDER BY seq", (task_id,))]


def events(runtime, task_id):
    with runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            'SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq',
            (task_id,))]


def state(runtime, task_id):
    with runtime.store.transaction() as db:
        task = tuple(db.execute(
            'SELECT status,stage_index,iteration,claimed_by,version,metadata '
            'FROM tasks WHERE id=?', (task_id,)).fetchone())
        sessions = [tuple(row) for row in db.execute(
            'SELECT id,task_id FROM sessions ORDER BY id')]
    return task, sessions, release_rows(runtime, task_id), events(runtime, task_id)


def workspace(record):
    if record['worktree'] is None:
        return None
    root = Path(record['worktree'])
    return (
        str(root), git(root, 'rev-parse', 'HEAD'), git(root, 'write-tree'),
        git(root, 'status', '--porcelain=v1'),
        (root / 'untracked-note.txt').read_bytes(),
    )


def prepare(project, kind):
    runtime, tools = draft(project, kind)
    tid = runtime.current_task()['id']
    create(tools, 'OTHER')
    edit(tools, tid, 'Initial acceptance')
    return runtime, tools, tid


def preserve(tools, name, *, note=False):
    runtime = tools.runtime
    current = runtime.current_task()
    path = None
    if note:
        path = runtime._roots(current)['task'] / 'continue-note.txt'
        path.write_bytes(b'Keep this working note.\n')
    packet = request('release', {
        'task_id': current['id'], 'request_id': name,
        'expected_version': current['_version'],
        'reason': 'Continue the same draft in its existing working area.',
    })
    saved = tools.invoke(packet)
    assert saved['status'] == 'ownership_released'
    return {'packet': packet, 'result': saved, 'note': path}


def replace_claim(tools, release):
    if release == 'create_other':
        create(tools, 'NEXT')
    else:
        edit(tools, 'OTHER', 'Continue the other draft')


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
@pytest.mark.parametrize('release', ['create_other', 'edit_other'])
def test_lawful_edit_allows_current_draft(project, kind, release):
    runtime, tools, tid = prepare(project, kind)
    saved = preserve(tools, 'old', note=True)
    edit(tools, tid, 'Use the current acceptance')
    replace_claim(tools, release)
    before = deepcopy(runtime.task_queries.record(tid))
    old_releases, old_events = release_rows(runtime, tid), events(runtime, tid)
    work = workspace(before)
    other_before = deepcopy(runtime.current_task())
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))

    acquired = select(receiver, tid)

    assert acquired['status'] == 'newborn'
    assert acquired['claimed_by'] == 'receiver'
    assert acquired['draft']['definition_of_done'] == ['Use the current acceptance']
    assert acquired['draft']['goal'] == (
        'Исправить double(2): получить 4.' if kind == 'restarted'
        else 'Keep this unfinished draft')
    assert acquired['draft'] == before['draft']
    assert release_rows(runtime, tid) == old_releases
    assert events(runtime, tid)[:len(old_events)] == old_events
    assert workspace(acquired) == work
    assert saved['note'].read_bytes() == b'Keep this working note.\n'
    assert runtime.current_task() == other_before


@pytest.mark.parametrize('kind', ['fresh', 'restarted'])
@pytest.mark.parametrize('extra_cycle', [False, True], ids=['once', 'again'])
def test_consumed_newer_does_not_revive_old_release(project, kind, extra_cycle):
    runtime, tools, tid = prepare(project, kind)
    old = preserve(tools, 'old', note=True)
    edit(tools, tid, 'Latest acceptance')
    preserve(tools, 'newer')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    assert select(receiver, tid)['draft']['definition_of_done'] == ['Latest acceptance']
    create(receiver, 'RECEIVER-NEXT')
    if extra_cycle:
        assert select(receiver, tid)['status'] == 'newborn'
        edit(receiver, 'RECEIVER-NEXT', 'Keep working elsewhere')
    before = deepcopy(runtime.task_queries.record(tid))
    rows, history = release_rows(runtime, tid), events(runtime, tid)
    work = workspace(before)
    other = deepcopy(receiver.runtime.current_task())
    last = WorkTools(WorkPoise(project['config_path'], 'last'))

    acquired = select(last, tid)
    replay = tools.invoke(old['packet'])

    assert replay['status'] == 'ownership_released' and replay['replayed'] is True
    assert acquired['status'] == 'newborn' and acquired['claimed_by'] == 'last'
    assert acquired['draft'] == before['draft']
    assert acquired['draft']['definition_of_done'] == ['Latest acceptance']
    assert runtime.task_queries.record(tid)['claimed_by'] == 'last'
    assert [json.loads(row[1])['request']['request_id'] for row in rows] == ['old', 'newer']
    assert release_rows(runtime, tid) == rows
    assert events(runtime, tid)[:len(history)] == history
    assert workspace(acquired) == work
    assert old['note'].read_bytes() == b'Keep this working note.\n'
    assert receiver.runtime.current_task() == other


@pytest.mark.parametrize('drift', ['none', 'worktree', 'branch'])
def test_current_draft_acquisition_preserves_shared_work(project, drift):
    runtime, tools, tid = prepare(project, 'restarted')
    preserve(tools, 'old')
    edit(tools, tid, 'Latest acceptance')
    preserve(tools, 'newer')
    root = Path(runtime.task_queries.record(tid)['worktree'])
    if drift == 'worktree':
        (root / 'foreign.txt').write_bytes(b'Preserve foreign work.\n')
    elif drift == 'branch':
        git(root, 'checkout', '-b', 'foreign-branch')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    before = deepcopy(runtime.task_queries.record(tid))
    work = workspace(before)
    rows = release_rows(runtime, tid)

    acquired = select(receiver, tid)

    assert acquired['claimed_by'] == 'receiver'
    assert acquired['draft'] == before['draft']
    assert workspace(acquired) == work
    assert release_rows(runtime, tid) == rows
    if drift == 'worktree':
        assert (root / 'foreign.txt').read_bytes() == b'Preserve foreign work.\n'
    elif drift == 'branch':
        assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'foreign-branch'


@pytest.mark.parametrize('change', ['version', 'claim'])
def test_newborn_acquisition_rechecks_observed_state(project, monkeypatch, change):
    runtime, tools, tid = prepare(project, 'fresh')
    create(tools, 'NEXT')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    editor = WorkTools(WorkPoise(project['config_path'], 'editor'))
    observed = []

    def intervene(preflight):
        assert preflight.task_id == tid and preflight.task_owner is None
        monkeypatch.setattr(receiver.runtime.ownership.commands, 'after_preflight', lambda _: None)
        edit(editor, tid, 'Changed during acquisition')
        if change == 'version':
            create(editor, 'EDITOR-NEXT')
        observed.append(state(runtime, tid))

    monkeypatch.setattr(receiver.runtime.ownership.commands, 'after_preflight', intervene)
    with pytest.raises(PoiseError, match='(?i)changed|version|owner|draft'):
        select(receiver, tid)

    assert len(observed) == 1
    assert state(runtime, tid) == observed[0]
    assert receiver.runtime.ownership.snapshot('receiver').task_id is None


def test_authoritative_restart_keeps_current_draft(project):
    runtime = WorkPoise(project['config_path'], 'owner')
    tools = WorkTools(runtime)
    active = bootstrap(tools, project)
    tid = active['task']
    (Path(active['worktree']) / 'untracked-note.txt').write_text('WIP to preserve\n')
    saved = preserve(tools, 'active-release', note=True)
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    assert select(receiver, tid)['status'] == 'active'
    restart(receiver, tid, receiver.runtime.current_task()['version'])
    edit(receiver, tid, 'Continue preparation after restart')
    create(receiver, 'NEXT')
    before = deepcopy(runtime.task_queries.record(tid))
    rows, history = release_rows(runtime, tid), events(runtime, tid)
    metadata = state(runtime, tid)[0][-1]
    work = workspace(before)
    third = WorkTools(WorkPoise(project['config_path'], 'third'))

    acquired = select(third, tid)

    assert acquired['status'] == 'newborn' and acquired['claimed_by'] == 'third'
    assert acquired['draft']['goal'] == 'Исправить double(2): получить 4.'
    assert acquired['draft']['definition_of_done'] == ['Continue preparation after restart']
    assert acquired['draft'] == before['draft']
    assert release_rows(runtime, tid) == rows
    assert events(runtime, tid)[:len(history)] == history
    assert state(runtime, tid)[0][-1] == metadata
    assert workspace(acquired) == work
    assert saved['note'].read_bytes() == b'Keep this working note.\n'
