"""Draft participants use ordinary release without material transport or readiness."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import bootstrap, configure, request
from conftest import WorkPoise
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


def release_request(h):
    current = h.current_task()
    return request('release', {
        'request_id': 'release-draft', 'task_id': current['id'],
        'expected_version': current['_version'],
        'reason': 'Continue the draft in the existing working area.',
    })


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
def test_draft_release_rejects_a_submitted_execution_result_without_mutation(project, kind):
    h, tools = draft(project, kind)
    before = deepcopy(h.current_task())
    rows = saved_rows(h, before['id'])
    packet = release_request(h)
    packet['input']['result'] = {'pretend': 'execution'}
    with pytest.raises(PoiseError, match="release input: unknown fields.*result"):
        tools.invoke(packet)
    assert h.current_task() == before
    assert saved_rows(h, before['id']) == rows


def test_draft_release_failure_keeps_claim_and_retries_same_intent(project):
    h, tools = draft(project, 'fresh')
    before = deepcopy(h.current_task())
    packet = release_request(h)
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_draft_release BEFORE UPDATE ON tasks WHEN NEW.claimed_by IS NULL BEGIN SELECT RAISE(ABORT,'draft-release-failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='draft-release-failure'):
        tools.invoke(packet)
    assert h.current_task() == before
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM journal WHERE event='ownership.ordinary_released'").fetchone()[0] == 0
        db.execute('DROP TRIGGER reject_draft_release')
    assert tools.invoke(packet)['status'] == 'ownership_released'
    assert h.current_task() is None


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_draft_release_and_acquisition_preserve_operator_metadata(project, kind):
    h, tools = draft(project, kind)
    tid = h.current_task()['id']
    with h.store.transaction() as db:
        metadata = json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0])
        metadata['operator_feedback'] = [{'new_information': 'Keep these unfinished working notes'}]
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(metadata), tid))
    assert tools.invoke(release_request(h))['status'] == 'ownership_released'
    with h.store.transaction() as db:
        assert json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0]) == metadata
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    other.invoke(request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    with h.store.transaction() as db:
        assert json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0]) == metadata


@pytest.mark.parametrize('kind', ['fresh', 'selected', 'restarted'])
def test_stale_draft_release_preserves_claim_and_work(project, kind):
    h, tools = draft(project, kind)
    before = deepcopy(h.current_task())
    rows = saved_rows(h, before['id'])
    packet = release_request(h)
    packet['input']['expected_version'] -= 1
    with pytest.raises(PoiseError, match='Release version conflict'):
        tools.invoke(packet)
    assert h.current_task() == before
    assert saved_rows(h, before['id']) == rows


def test_foreign_actor_cannot_release_the_owned_draft(project):
    h, tools = draft(project, 'fresh')
    before = deepcopy(h.current_task())
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    with pytest.raises(PoiseError, match='Release requires the named Task owned by the current caller'):
        other.invoke(release_request(h))
    assert h.current_task() == before
    assert other.runtime.current_task() is None


def test_newborn_acquisition_failure_rolls_back_claim_and_worktree_binding(project):
    h, tools = draft(project, 'restarted')
    tid = h.current_task()['id']
    assert tools.invoke(release_request(h))['status'] == 'ownership_released'
    before = deepcopy(h.task_queries.record(tid))
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_newborn_acquisition BEFORE UPDATE ON tasks WHEN NEW.claimed_by='receiver' BEGIN SELECT RAISE(ABORT,'newborn-acquisition-failure'); END")
    other = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    packet = request('bootstrap', {'task': {'id': tid}, 'decision': None, 'feedback': None, 'rework_stage': None})
    with pytest.raises(sqlite3.IntegrityError, match='newborn-acquisition-failure'):
        other.invoke(packet)
    assert h.task_queries.record(tid) == before
    assert h.ownership.snapshot('receiver').task_id is None
    assert h.ownership.snapshot('receiver').worktree_task_id is None
    with h.store.transaction() as db:
        db.execute('DROP TRIGGER fail_newborn_acquisition')
    assert other.invoke(packet)['status'] == 'newborn'


def test_verified_history_survives_restart_inspection_and_ordinary_draft_release(project):
    from batch.helpers import result, verify
    from conftest import add_test
    h = WorkPoise(project['config_path'], 'owner')
    tools = WorkTools(h)
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    assert verify(tools, result(context))['status'] == 'verified'
    restart(tools, 'T1', h.current_task()['_version'])
    before = saved_rows(h, 'T1')
    assert before['submissions'] and before['task_results'] and before['evidence']
    view = tools.invoke(request('show', {'queries': [{'id': 'registry', 'kind': 'verification_registry'}]}))['results'][0]['value']
    assert view['historical_registry'] is not None
    assert view['active_contract'] is False and view['current'] == []
    assert tools.invoke(release_request(h))['status'] == 'ownership_released'
    assert saved_rows(h, 'T1') == before
