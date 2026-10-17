"""A ready member of an unpublished Sprint can continue its current draft."""
from copy import deepcopy
import json
from pathlib import Path

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from runtime_services.test_newborn_handoff import configure_handoff, handoff
from runtime_services.test_newborn_handoff_eligibility import (
    events, handoffs, select, state,
)
from sprints.helpers import changes, setup
from tasks.test_newborn_lifecycle import create, edit, sprint_complete_patch, task_action


def test_ready_unpublished_member_continues_after_lawful_edit(project):
    setup(project)
    configure_handoff(project)
    owner = WorkTools(WorkPoise(project['config_path'], 'owner'))
    owner.invoke(request('sprint', {
        'action': 'draft', 'sprint_id': 'S', 'request_id': 'draft-ready-sprint',
        'expected_revision': None, 'template': {'id': 'basic', 'version': '1'},
        'changes': changes([]),
    }))
    born = create(owner, 'MEMBER', 'S')
    edit(owner, 'MEMBER', born['revision'],
         sprint_complete_patch(project, 'MEMBER', 'S') | {'goal_type': 'development'},
         'complete-member')
    runtime = owner.runtime
    runtime.runtime.mkdir(parents=True, exist_ok=True)
    note = runtime.runtime / 'sprint-ready-note.txt'
    note.write_bytes(b'Continue the current Sprint member.\n')
    saved = handoff(owner, request_id='preserve-member', artifact_paths=[str(note)])
    receipt_path = Path(saved['receipt_path'])
    preserved_note = Path(saved['preserved_artifacts'][0])
    receipt_bytes = receipt_path.read_bytes()
    assert preserved_note.read_bytes() == b'Continue the current Sprint member.\n'

    editor = WorkTools(WorkPoise(project['config_path'], 'editor'))
    current = runtime.task_queries.record('MEMBER')
    edited = edit(editor, 'MEMBER', current['revision'],
                  {'definition_of_done': ['Continue the ready Sprint member']},
                  'refine-member')
    ready = task_action(editor, action='ready', request_id='ready-member',
                        task_id='MEMBER', expected_revision=edited['revision'])
    assert ready['status'] == 'newborn' and ready['ready'] is True
    create(editor, 'OTHER')
    before = deepcopy(runtime.task_queries.record('MEMBER'))
    assert before['status'] == 'newborn' and before['ready'] is True
    assert before['claimed_by'] is None and before['worktree'] is None
    assert before['sprint_id'] == 'S'
    assert before['draft']['goal'] == 'Результат MEMBER'
    old_events, old_handoffs = events(runtime, 'MEMBER'), handoffs(runtime, 'MEMBER')
    assert [item['event'] for item in before['history']] == [
        'created_newborn', 'newborn_edited', 'ownership_released',
        'newborn_edited', 'newborn_ready', 'ownership_released',
    ]
    metadata = state(runtime, 'MEMBER')[0][5]
    with runtime.store.transaction() as db:
        sprint = db.execute('SELECT data FROM sprints WHERE id=?', ('S',)).fetchone()[0]
    assert json.loads(sprint)['aggregate']['state'] == 'draft'
    other = deepcopy(editor.runtime.current_task())
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))

    acquired = select(receiver, 'MEMBER')

    assert acquired['status'] == 'newborn' and acquired['ready'] is True
    assert acquired['claimed_by'] == 'receiver' and acquired['sprint_id'] == 'S'
    assert acquired['draft'] == before['draft']
    assert acquired['draft']['goal'] == 'Результат MEMBER'
    assert acquired['draft']['definition_of_done'] == ['Continue the ready Sprint member']
    assert acquired['worktree'] is None
    assert acquired['history'][:-1] == before['history']
    assert acquired['history'][-1]['event'] == 'ownership_acquired'
    assert events(runtime, 'MEMBER')[:-1] == old_events
    assert handoffs(runtime, 'MEMBER') == old_handoffs
    assert state(runtime, 'MEMBER')[0][5] == metadata
    with runtime.store.transaction() as db:
        assert db.execute('SELECT data FROM sprints WHERE id=?', ('S',)).fetchone()[0] == sprint
    assert editor.runtime.current_task() == other
    assert receiver.runtime.ownership.snapshot('receiver').task_id == 'MEMBER'
    assert receiver.runtime.ownership.snapshot('receiver').worktree_task_id is None
    assert receipt_path.read_bytes() == receipt_bytes
    assert preserved_note.read_bytes() == b'Continue the current Sprint member.\n'
