"""Current draft acquisition must not resurrect historical handoffs.

Integration subject: WorkTools bootstrap/edit/handoff, SQLite ownership and Git
preservation. Expectations are test literals and exact pre-operation snapshots.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_newborn_handoff import draft, handoff


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


def handoffs(runtime, task_id):
    with runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            'SELECT actor,request_id,state,data FROM handoffs '
            'WHERE task_id=? ORDER BY seq', (task_id,))]


def events(runtime, task_id):
    with runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            'SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq',
            (task_id,))]


def state(runtime, task_id):
    """Only business state; diagnostic journal/message accounting is separate."""
    with runtime.store.transaction() as db:
        task = tuple(db.execute(
            'SELECT status,stage_index,iteration,claimed_by,version,metadata '
            'FROM tasks WHERE id=?', (task_id,)).fetchone())
        sessions = [tuple(row) for row in db.execute(
            'SELECT id,task_id FROM sessions ORDER BY id')]
    return task, sessions, handoffs(runtime, task_id), events(runtime, task_id)


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
    # Both replacement routes will have an existing, free draft available.
    create(tools, 'OTHER')
    edit(tools, tid, 'Initial acceptance')
    return runtime, tools, tid


def preserve(tools, name, *, note=False):
    paths = []
    if note:
        runtime = tools.runtime
        runtime.runtime.mkdir(parents=True, exist_ok=True)
        path = runtime.runtime / 'continue-note.txt'
        path.write_text('Keep this working note.\n')
        paths.append(str(path))
    return handoff(tools, request_id=name,
                   commit_message='Preserve the current draft work',
                   artifact_paths=paths)


def replace_claim(tools, release):
    if release == 'create_other':
        create(tools, 'NEXT')
    else:
        edit(tools, 'OTHER', 'Continue the other draft')


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
@pytest.mark.parametrize('release', ['create_other', 'edit_other'])
def test_lawful_edit_allows_current_draft(project, kind, release):
    runtime, tools, tid = prepare(project, kind)
    receipt = preserve(tools, 'old', note=True)
    note = Path(receipt['preserved_artifacts'][0])
    receipt_bytes = Path(receipt['receipt_path']).read_bytes()
    edit(tools, tid, 'Use the current acceptance')
    replace_claim(tools, release)
    before = deepcopy(runtime.task_queries.record(tid))
    old_handoffs, old_events = handoffs(runtime, tid), events(runtime, tid)
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
    assert handoffs(runtime, tid) == old_handoffs
    assert events(runtime, tid)[:len(old_events)] == old_events
    assert workspace(acquired) == work
    assert note.read_bytes() == b'Keep this working note.\n'
    assert Path(receipt['receipt_path']).read_bytes() == receipt_bytes
    assert runtime.current_task() == other_before


@pytest.mark.parametrize('kind', ['fresh', 'restarted'])
@pytest.mark.parametrize('extra_cycle', [False, True], ids=['once', 'again'])
def test_consumed_newer_does_not_revive_old_release(project, kind, extra_cycle):
    runtime, tools, tid = prepare(project, kind)
    old = preserve(tools, 'old', note=True)
    old_row = handoffs(runtime, tid)[0]
    edit(tools, tid, 'Latest acceptance')
    newer = preserve(tools, 'newer')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    assert select(receiver, tid)['draft']['definition_of_done'] == ['Latest acceptance']
    create(receiver, 'RECEIVER-NEXT')
    if extra_cycle:
        assert select(receiver, tid)['status'] == 'newborn'
        edit(receiver, 'RECEIVER-NEXT', 'Keep working elsewhere')
    before = deepcopy(runtime.task_queries.record(tid))
    rows, history = handoffs(runtime, tid), events(runtime, tid)
    work = workspace(before)
    last = WorkTools(WorkPoise(project['config_path'], 'last'))

    acquired = select(last, tid)

    assert acquired['status'] == 'newborn' and acquired['claimed_by'] == 'last'
    assert acquired['draft'] == before['draft']
    assert acquired['draft']['definition_of_done'] == ['Latest acceptance']
    assert [row[2] for row in handoffs(runtime, tid)] == ['released', 'resumed']
    assert handoffs(runtime, tid) == rows and rows[0] == old_row
    assert events(runtime, tid)[:len(history)] == history
    assert workspace(acquired) == work
    assert Path(old['preserved_artifacts'][0]).read_bytes() == b'Keep this working note.\n'
    assert Path(newer['receipt_path']).is_file()


@pytest.mark.parametrize('drift', ['none', 'worktree', 'bundle', 'branch'])
def test_applicable_newer_handoff_keeps_saved_work_guard(project, drift):
    runtime, tools, tid = prepare(project, 'restarted')
    preserve(tools, 'old')
    edit(tools, tid, 'Latest acceptance')
    receipt = preserve(tools, 'newer')
    root = Path(receipt['worktree'])
    if drift == 'worktree':
        (root / 'foreign.txt').write_text('Preserve foreign work.\n')
    elif drift == 'bundle':
        Path(receipt['bundle_path']).write_bytes(b'Changed bundle')
    elif drift == 'branch':
        git(root, 'checkout', '-b', 'foreign-branch')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    before, work = state(runtime, tid), workspace(runtime.task_queries.record(tid))

    if drift == 'none':
        assert select(receiver, tid)['claimed_by'] == 'receiver'
        assert [row[2] for row in handoffs(runtime, tid)] == ['released', 'resumed']
    else:
        with pytest.raises(PoiseError, match='(?i)worktree.*changed|bundle.*changed'):
            select(receiver, tid)
        assert state(runtime, tid) == before
    assert workspace(runtime.task_queries.record(tid)) == work


@pytest.mark.parametrize('damage', ['gap', 'unknown_event'])
def test_unexplained_draft_history_refuses(project, damage):
    runtime, tools, tid = prepare(project, 'fresh')
    preserve(tools, 'old')
    edit(tools, tid, 'Current acceptance')
    create(tools, 'NEXT')
    # Deliberately corrupt only this isolated test fixture, after lawful setup.
    with runtime.store.transaction() as db:
        row = db.execute(
            "SELECT seq,data FROM task_events WHERE task_id=? "
            "AND json_extract(data,'$.event')='newborn_edited' ORDER BY seq DESC LIMIT 1",
            (tid,)).fetchone()
        if damage == 'gap':
            db.execute('DELETE FROM task_events WHERE seq=?', (row['seq'],))
        else:
            event = json.loads(row['data'])
            event['event'] = 'unexplained_change'
            db.execute('UPDATE task_events SET data=? WHERE seq=?',
                       (json.dumps(event), row['seq']))
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    before = state(runtime, tid)

    with pytest.raises(PoiseError, match='(?i)handoff|draft|history|version'):
        select(receiver, tid)

    assert state(runtime, tid) == before


@pytest.mark.parametrize('change', ['version', 'claim'])
def test_newborn_acquisition_rechecks_observed_state(project, monkeypatch, change):
    runtime, tools, tid = prepare(project, 'fresh')
    create(tools, 'NEXT')  # No preserved handoff: exercise ordinary acquisition.
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

    with pytest.raises(PoiseError, match='(?i)changed|version|owner|handoff|draft'):
        select(receiver, tid)

    assert len(observed) == 1, 'The actual acquisition boundary must be exercised'
    assert state(runtime, tid) == observed[0]
    assert receiver.runtime.ownership.snapshot('receiver').task_id is None


def test_current_handoff_rechecks_after_material_validation(project, monkeypatch):
    runtime, tools, tid = prepare(project, 'fresh')
    preserve(tools, 'current')
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    editor = WorkTools(WorkPoise(project['config_path'], 'editor'))
    validate = receiver.runtime.handoff_tools.validate_resume
    observed = []

    def intervene(data):
        record = validate(data)
        edit(editor, tid, 'Changed after material validation')
        create(editor, 'EDITOR-NEXT')
        observed.append(state(runtime, tid))
        return record

    monkeypatch.setattr(receiver.runtime.handoff_tools, 'validate_resume', intervene)

    with pytest.raises(PoiseError, match='Task changed after preserved handoff'):
        select(receiver, tid)

    assert len(observed) == 1, 'Real saved-material validation must have succeeded'
    assert state(runtime, tid) == observed[0]
    assert receiver.runtime.ownership.snapshot('receiver').task_id is None
