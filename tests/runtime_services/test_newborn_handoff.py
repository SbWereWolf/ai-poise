"""A draft can be inspected and safely saved without a fabricated execution stage."""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import bootstrap, configure, request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_task_restart import restart


def draft(project, kind):
    configure(project)
    h = WorkPoise(project['config_path'], 'owner')
    tools = WorkTools(h)
    if kind == 'restarted':
        context = bootstrap(tools, project)
        root = Path(context['worktree'])
        (root / 'untracked-note.txt').write_text('WIP to preserve\n')
        restart(tools, 'T1', h.current_task()['version'])
    else:
        tools.invoke(request('task', {'action': 'create', 'request_id': 'draft-create', 'task_id': 'DRAFT', 'sprint_id': None}))
        data = h.current_task()
        tools.invoke(request('task', {
            'action': 'edit', 'request_id': 'draft-edit', 'task_id': data['id'],
            'expected_revision': data['revision'],
            'patch': {'goal': 'Keep this unfinished draft', **({'goal_type': 'development'} if kind == 'selected' else {})},
            'remove': [],
        }))
    return h, tools


def handoff(tools, **changes):
    return tools.invoke(request('handoff', {
        'request_id': 'save-draft', 'reason': 'Continue the draft in a later session',
        'result': None, 'commit_message': None, 'artifact_paths': [], **changes,
    }))


def saved_rows(h, task_id):
    with h.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} WHERE task_id=? ORDER BY rowid', (task_id,))]
                for table in ('submissions', 'task_results', 'evidence', 'task_methods', 'task_execution', 'task_workflows')}


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_newborn_registry_is_explicitly_inactive_without_loading_contract(project, kind):
    h, tools = draft(project, kind)
    before = deepcopy(h.current_task())
    history = saved_rows(h, before['id'])
    out = tools.invoke(request('show', {'queries': [{'id': 'registry', 'kind': 'verification_registry'}]}))
    item = out['results'][0]['value']
    assert item['task'] == before['id']
    assert item['status'] == 'read_only'
    assert item['task_status'] == 'newborn'
    assert item['active_contract'] is False
    assert item['revision'] is None
    assert item['current'] == []
    assert item['draft_methods'] == before['draft'].get('methods', [])
    assert h.current_task() == before
    assert saved_rows(h, before['id']) == history


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_public_draft_handoff_preserves_and_releases_without_readiness(project, kind):
    h, tools = draft(project, kind)
    before = deepcopy(h.current_task()); tid = before['id']
    immutable = saved_rows(h, tid)
    receipt = handoff(tools, commit_message='Save restarted draft WIP' if kind == 'restarted' else None)
    assert receipt['status'] == 'handed_off'
    assert receipt['stage'] is None
    assert receipt['task_status'] == 'newborn'
    assert receipt['verified'] is False
    assert h.current_task() is None
    after = h.task_queries.record(tid)
    assert after['status'] == 'newborn'
    assert after['draft'] == before['draft']
    assert after['sprint_id'] == before['sprint_id']
    assert after['_version'] == before['_version'] + 1
    assert after['history'][:-1] == before['history']
    assert after['history'][-1]['event'] == 'ownership_released'
    assert saved_rows(h, tid) == immutable
    assert h.ownership.snapshot('owner').worktree_task_id is None
    assert Path(receipt['receipt_path']).is_file()
    if kind == 'restarted':
        assert (Path(before['worktree']) / 'untracked-note.txt').read_text() == 'WIP to preserve\n'
        assert git(Path(before['worktree']), 'rev-parse', 'HEAD') == receipt['commit']
        assert Path(receipt['bundle_path']).is_file()
    else:
        assert receipt['worktree'] is None and receipt['bundle_path'] is None
        assert receipt['tree'] is None and receipt['commit'] is None
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    acquired = other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert acquired['status'] == 'newborn' and acquired['draft'] == before['draft']
    assert acquired['worktree'] == before['worktree']
    replay = handoff(tools, commit_message='Save restarted draft WIP' if kind == 'restarted' else None)
    assert replay['replayed'] is True
    assert h.task_queries.record(tid)['claimed_by'] == 'receiver'
    with pytest.raises(PoiseError, match='(?i)request.*different|identity conflict'):
        handoff(tools, reason='changed meaning')


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_draft_result_is_rejected_before_preservation_or_claim_mutation(project, kind):
    h, tools = draft(project, kind)
    before = deepcopy(h.current_task()); rows = saved_rows(h, before['id'])
    with pytest.raises(PoiseError, match='(?i)newborn.*result|draft.*result'):
        handoff(tools, result={'pretend': 'execution'})
    assert h.current_task() == before
    assert saved_rows(h, before['id']) == rows
    with h.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM handoffs').fetchone()[0] == 0


def test_draft_handoff_release_failure_keeps_claim_and_retries_same_intent(project):
    h, tools = draft(project, 'fresh'); before = deepcopy(h.current_task())
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_draft_release BEFORE UPDATE ON tasks WHEN NEW.claimed_by IS NULL BEGIN SELECT RAISE(ABORT,'draft-release-failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='draft-release-failure'):
        handoff(tools)
    assert h.current_task() == before
    record = h.handoff_tools.commands.lookup('owner', 'save-draft')
    assert record['state'] == 'preparing'
    with h.store.transaction() as db:
        db.execute('DROP TRIGGER reject_draft_release')
    assert handoff(tools)['status'] == 'handed_off'
    assert h.current_task() is None


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_draft_handoff_and_acquisition_preserve_operator_metadata(project, kind):
    import json
    h, tools = draft(project, kind); tid = h.current_task()['id']
    with h.store.transaction() as db:
        metadata = json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0])
        metadata['operator_feedback'] = [{'new_information': 'Do not discard recovery notes'}]
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(metadata), tid))
    handoff(tools, commit_message='Save restarted draft WIP' if kind == 'restarted' else None)
    with h.store.transaction() as db:
        assert json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0]) == metadata
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    with h.store.transaction() as db:
        assert json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0]) == metadata
    assert h.handoff_tools.commands.lookup('owner', 'save-draft')['state'] == 'resumed'


@pytest.mark.parametrize('drift', ['worktree', 'bundle', 'branch'])
def test_newborn_resume_rejects_changed_saved_work_before_acquiring(project, drift):
    h, tools = draft(project, 'restarted'); tid = h.current_task()['id']
    receipt = handoff(tools, commit_message='Save restarted draft WIP')
    root = Path(receipt['worktree'])
    if drift == 'worktree': (root / 'external.txt').write_text('foreign WIP')
    elif drift == 'bundle': Path(receipt['bundle_path']).write_bytes(b'changed')
    else: git(root, 'checkout', '-b', 'foreign-branch')
    before = deepcopy(h.task_queries.record(tid))
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    with pytest.raises(PoiseError, match='(?i)worktree.*changed|bundle.*changed'):
        other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert h.task_queries.record(tid) == before
    assert h.ownership.snapshot('receiver').task_id is None
    assert h.ownership.snapshot('receiver').worktree_task_id is None


def test_newborn_resume_failure_rolls_back_claim_binding_and_receipt(project):
    h, tools = draft(project, 'restarted'); tid = h.current_task()['id']
    handoff(tools, commit_message='Save restarted draft WIP'); before = deepcopy(h.task_queries.record(tid))
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_newborn_resume BEFORE UPDATE ON handoffs WHEN NEW.state='resumed' BEGIN SELECT RAISE(ABORT,'newborn-resume-failure'); END")
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    with pytest.raises(sqlite3.IntegrityError, match='newborn-resume-failure'):
        other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert h.task_queries.record(tid) == before
    assert h.ownership.snapshot('receiver').task_id is None
    assert h.ownership.snapshot('receiver').worktree_task_id is None
    with h.store.transaction() as db: db.execute('DROP TRIGGER fail_newborn_resume')
    assert other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))['status'] == 'newborn'


def test_fresh_draft_can_preserve_selected_runtime_note_before_cleanup(project):
    h, tools = draft(project, 'fresh'); h.runtime.mkdir(parents=True, exist_ok=True)
    note = h.runtime / 'operator-note.txt'; note.write_text('Need to continue this exact draft')
    receipt = handoff(tools, artifact_paths=[str(note)])
    assert len(receipt['preserved_artifacts']) == 1
    assert Path(receipt['preserved_artifacts'][0]).read_text() == 'Need to continue this exact draft'
    assert not h.runtime.exists()


def test_restarted_draft_requires_wip_message_and_preserves_index_on_rejection(project):
    h, tools = draft(project, 'restarted'); before = deepcopy(h.current_task())
    tree = Path(before['worktree']); git_before = (git(tree, 'rev-parse', 'HEAD'), git(tree, 'write-tree'))
    with pytest.raises(PoiseError, match='WIP requires explicit'):
        handoff(tools)
    assert h.current_task() == before
    assert (git(tree, 'rev-parse', 'HEAD'), git(tree, 'write-tree')) == git_before


def test_verified_history_survives_restart_inspection_and_draft_handoff(project):
    from batch.helpers import result, verify
    from conftest import add_test
    h = WorkPoise(project['config_path'], 'owner'); tools = WorkTools(h)
    context = bootstrap(tools, project); add_test(context['worktree'])
    assert verify(tools, result(context))['status'] == 'verified'
    restart(tools, 'T1', h.current_task()['_version'])
    before = saved_rows(h, 'T1')
    assert before['submissions'] and before['task_results'] and before['evidence']
    view = tools.invoke(request('show', {'queries': [{'id': 'registry', 'kind': 'verification_registry'}]}))['results'][0]['value']
    assert view['historical_registry'] is not None
    assert view['active_contract'] is False and view['current'] == []
    assert handoff(tools)['status'] == 'handed_off'
    assert saved_rows(h, 'T1') == before
