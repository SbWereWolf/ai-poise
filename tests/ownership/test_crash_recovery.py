"""Explicit operator recovery of abandoned claims through the public work API."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request, configure
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.infrastructure.sqlite.ownership import SqliteOwnershipRepository
from poise.modules.foundation.errors import PoiseError
from poise.modules.ownership.domain import Liveness
from ownership.test_atomic_ownership import _bootstrap, _configure_process, _fingerprint
from tasks.test_newborn_lifecycle import create, edit


def operator(project):
    return WorkTools(WorkPoise(project['config_path'], 'recovery-operator'))


def inspect(tools, ids):
    return tools.invoke(request('show', {'queries': [
        {'id': 'recovery', 'kind': 'ownership_recovery', 'task_ids': ids}
    ]}))['results'][0]['value']


def intent(tools, ids, request_id='crash-1'):
    view = inspect(tools, ids)
    return {'mode': 'after_crash', 'request_id': request_id, 'task_ids': ids,
            'expected_snapshot': view['expected_snapshot'],
            'reason': 'The original environment crashed; this is its isolated recovered copy.',
            'writers_stopped': True,
            'authorization': {'role': 'user', 'decision': 'User authorized revoking these abandoned claims.'}}


def rows(tools):
    with tools.runtime.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in ('tasks', 'sessions', 'task_events', 'task_execution',
                              'evidence', 'task_proofs', 'handoffs', 'action_runs')}


def seed_newborn(project, task_id='ORPHAN', actor='lost-planner'):
    configure(project)
    old = WorkTools(WorkPoise(project['config_path'], actor))
    create(old, task_id)
    edit(old, task_id, 0, {'goal': 'Keep this uncompleted draft'}, 'draft-'+task_id)
    return old


def test_newborn_recovery_preserves_draft_and_enables_same_task_restart(project):
    old = seed_newborn(project)
    tools = operator(project)
    before = old.runtime.task_queries.record('ORPHAN')
    with pytest.raises(PoiseError, match='uncertain'):
        tools.runtime.ownership.acquire_task('ORPHAN')
    p = intent(tools, ['ORPHAN'])
    result = tools.invoke(request('recover_ownership', p))
    assert result['status'] == 'ownership_recovered'
    assert result['task_ids'] == ['ORPHAN']
    after = tools.runtime.task_queries.record('ORPHAN')
    assert after['claimed_by'] is None and after['status'] == 'newborn'
    assert after['revision'] == before['revision'] + 1
    assert after['draft'] == before['draft']
    tools.runtime.ownership.acquire_task('ORPHAN')
    current = tools.runtime.task_queries.record('ORPHAN')
    out = tools.invoke(request('task', {
        'action': 'restart', 'request_id': 'restart-after-recovery', 'task_id': 'ORPHAN',
        'expected_version': current['revision'], 'reason': 'Continue the preserved draft',
        'authorization': 'User authorized recovery and replanning'}))
    assert out['status'] == 'newborn'
    assert out['draft']['goal'] == before['draft']['goal']


@pytest.mark.parametrize('state', [Liveness.UNCERTAIN, Liveness.LIVE, Liveness.DEAD])
def test_operator_decision_does_not_fabricate_session_end(project, state):
    seed_newborn(project)
    tools = operator(project)
    tools.runtime.ownership.commands.liveness = lambda _: state
    p = intent(tools, ['ORPHAN'])
    tools.invoke(request('recover_ownership', p))
    assert tools.runtime.ownership.commands.liveness('lost-planner') == state
    with tools.runtime.store.transaction() as db:
        records = [json.loads(r[0]) for r in db.execute(
            "SELECT data FROM journal WHERE event='ownership.crash_recovered'")]
    assert len(records) == 1
    assert records[0]['actor'] == 'recovery-operator'
    assert records[0]['observed_liveness'] == {'lost-planner': state.value}
    assert records[0]['request']['authorization'] == p['authorization']


@pytest.mark.parametrize('bad', ['no_decision', 'reviewer', 'no_stop', 'bad_mode', 'duplicate_id'])
def test_recovery_requires_explicit_user_decision_and_quiescence(project, bad):
    seed_newborn(project); tools = operator(project)
    p = intent(tools, ['ORPHAN']); before = rows(tools)
    if bad == 'no_decision': p['authorization']['decision'] = ''
    if bad == 'reviewer': p['authorization']['role'] = 'reviewer'
    if bad == 'no_stop': p['writers_stopped'] = False
    if bad == 'bad_mode': p['mode'] = 'force'
    if bad == 'duplicate_id': p['task_ids'].append('ORPHAN')
    with pytest.raises(PoiseError): tools.invoke(request('recover_ownership', p))
    assert rows(tools) == before


def test_replay_never_releases_the_new_owner(project):
    seed_newborn(project); tools = operator(project); p = intent(tools, ['ORPHAN'])
    tools.invoke(request('recover_ownership', p))
    new = WorkPoise(project['config_path'], 'new-owner'); new.ownership.acquire_task('ORPHAN')
    before = rows(tools)
    assert tools.invoke(request('recover_ownership', p))['replayed'] is True
    assert rows(tools) == before
    changed = deepcopy(p); changed['reason'] += ' changed'
    with pytest.raises(PoiseError, match='conflict'):
        tools.invoke(request('recover_ownership', changed))
    assert rows(tools) == before


def test_revoked_session_cannot_reclaim_even_after_new_owner_releases(project):
    old = seed_newborn(project); tools = operator(project); p = intent(tools, ['ORPHAN'])
    tools.invoke(request('recover_ownership', p))
    tools.runtime.ownership.acquire_task('ORPHAN')
    tools.runtime.ownership.release_task('ORPHAN')
    before = rows(tools)
    with pytest.raises(PoiseError, match='revoked'):
        old.runtime.ownership.acquire_task('ORPHAN')
    with pytest.raises(PoiseError, match='revoked'):
        old.runtime.ownership.acquire_worktree('ORPHAN')
    assert rows(tools) == before


def test_snapshot_drift_rejects_entire_batch(project):
    first = seed_newborn(project, 'ONE', 'lost-one')
    seed_newborn(project, 'TWO', 'lost-two')
    tools = operator(project); p = intent(tools, ['ONE', 'TWO'])
    current = first.runtime.task_queries.record('ONE')
    edit(first, 'ONE', current['revision'], {'goal': 'Newer legitimate work'}, 'later-edit')
    before = rows(tools)
    with pytest.raises(PoiseError, match='snapshot'):
        tools.invoke(request('recover_ownership', p))
    assert rows(tools) == before


def test_release_and_audit_failure_roll_back_together(project, monkeypatch):
    seed_newborn(project); tools = operator(project); p = intent(tools, ['ORPHAN'])
    before = rows(tools)
    def fail(*args, **kwargs): raise PoiseError('injected receipt failure')
    monkeypatch.setattr(SqliteOwnershipRepository, 'record_crash_recovery', fail)
    with pytest.raises(PoiseError, match='injected'):
        tools.invoke(request('recover_ownership', p))
    assert rows(tools) == before
    assert tools.runtime.ownership.snapshot('lost-planner').task_id == 'ORPHAN'


def test_active_recovery_preserves_worktree_files_execution_and_evidence(project):
    _configure_process(project, True)
    old, context = _bootstrap(project, 'lost-worker', 'ACTIVE')
    worktree = Path(context['worktree']); (worktree/'local-wip.txt').write_text('keep me')
    fingerprint = _fingerprint(worktree)
    tools = operator(project); before = rows(tools)
    p = intent(tools, ['ACTIVE']); tools.invoke(request('recover_ownership', p))
    after = rows(tools)
    assert after['task_execution'] == before['task_execution']
    assert after['evidence'] == before['evidence']
    assert after['handoffs'] == before['handoffs']
    assert _fingerprint(worktree) == fingerprint
    assert tools.runtime.ownership.snapshot('lost-worker').worktree_task_id is None
    assert tools.runtime.task_queries.record('ACTIVE')['status'] == 'active'
    resumed = tools.invoke(request('bootstrap', {'task': {'id': 'ACTIVE'},
        'decision': None, 'feedback': None, 'rework_stage': None}))
    assert resumed['task'] == 'ACTIVE' and resumed['worktree'] == str(worktree)
    assert tools.runtime.ownership.snapshot('recovery-operator').worktree_task_id == 'ACTIVE'
    assert _fingerprint(worktree) == fingerprint


def test_recover_one_task_preserves_old_owners_independent_worktree(project):
    _configure_process(project, True)
    tree_tools, _ = _bootstrap(project, 'tree-seed', 'TREE')
    tree_tools.runtime.ownership.release_task('TREE')
    _configure_process(project, False)
    old, _ = _bootstrap(project, 'lost-worker', 'TASK')
    old.runtime.ownership.acquire_worktree('TREE')
    tools = operator(project); p = intent(tools, ['TASK'])
    tools.invoke(request('recover_ownership', p))
    assert tools.runtime.ownership.snapshot('lost-worker').task_id is None
    assert tools.runtime.ownership.snapshot('lost-worker').worktree_task_id == 'TREE'


def test_batch_recovery_and_no_claim_noop_are_explicit(project):
    seed_newborn(project, 'ONE', 'lost-one'); seed_newborn(project, 'TWO', 'lost-two')
    tools = operator(project); p = intent(tools, ['TWO', 'ONE'])
    out = tools.invoke(request('recover_ownership', p))
    assert out['task_ids'] == ['ONE', 'TWO']
    assert tools.runtime.ownership.snapshot('lost-one').task_id is None
    assert tools.runtime.ownership.snapshot('lost-two').task_id is None
    with pytest.raises(PoiseError, match='no abandoned claims'):
        tools.invoke(request('recover_ownership', intent(tools, ['ONE'], 'empty')))
    with tools.runtime.store.transaction() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 13
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert list(db.execute('PRAGMA foreign_key_check')) == []


def test_inspection_materializes_safe_recovery_packet_without_fabricating_authority(project):
    seed_newborn(project); tools = operator(project)
    view = inspect(tools, ['ORPHAN'])
    packet = view['recovery_template']
    assert packet['operation'] == 'recover_ownership'
    assert packet['input']['task_ids'] == ['ORPHAN']
    assert packet['input']['expected_snapshot'] == view['expected_snapshot']
    assert packet['input']['request_id']
    assert packet['input']['writers_stopped'] is False
    assert packet['input']['authorization'] == {'role': 'user', 'decision': ''}
    with pytest.raises(PoiseError): tools.invoke(packet)
    packet['input'].update(writers_stopped=True, reason='Isolated restoration confirmed')
    packet['input']['authorization']['decision'] = 'User explicitly authorized recovery'
    assert tools.invoke(packet)['status'] == 'ownership_recovered'


def test_pending_check_survives_reset_and_execution_drift_invalidates_snapshot(project):
    _configure_process(project, True)
    old, context = _bootstrap(project, 'lost-worker', 'ACTIVE')
    tools = operator(project); stale = intent(tools, ['ACTIVE'])
    # Inject an unknown-outcome fixture through its execution repository. Recovery
    # must preserve it, not manufacture a check receipt or repeat the action.
    with old.runtime.store.unit_of_work() as uow:
        data, version = uow.execution.load('ACTIVE')
        data['pending'] = {'kind': 'checks', 'execution_key': 'lost-check', 'request': {'id': 'attempt'}}
        uow.execution.save('ACTIVE', data, version)
    before = rows(tools)
    with pytest.raises(PoiseError, match='snapshot'):
        tools.invoke(request('recover_ownership', stale))
    assert rows(tools) == before
    current = intent(tools, ['ACTIVE'], 'after-pending-inspection')
    tools.invoke(request('recover_ownership', current))
    after = rows(tools)
    for name in ('task_execution', 'evidence', 'task_proofs', 'handoffs', 'action_runs'):
        assert after[name] == before[name]


def test_revoked_old_actor_cannot_resume_edit_or_restart_draft(project):
    old = seed_newborn(project); tools = operator(project)
    tools.invoke(request('recover_ownership', intent(tools, ['ORPHAN'])))
    before = rows(tools)
    with pytest.raises(PoiseError, match='revoked'):
        old.invoke(request('bootstrap', {'task': {'id': 'ORPHAN'}, 'decision': None,
                                        'feedback': None, 'rework_stage': None}))
    n = old.runtime.task_queries.record('ORPHAN')
    with pytest.raises(PoiseError, match='revoked'):
        edit(old, 'ORPHAN', n['revision'], {'goal': 'Stale actor resumed'}, 'stale-edit')
    with pytest.raises(PoiseError, match='revoked'):
        old.invoke(request('task', {'action': 'restart', 'request_id': 'stale-restart',
            'task_id': 'ORPHAN', 'expected_version': n['revision'], 'reason': 'Stale restart',
            'authorization': {'role': 'user', 'decision': 'Old saved decision'}}))
    assert rows(tools) == before


def test_restored_request_replay_remains_historical_in_another_runtime(project):
    seed_newborn(project); tools = operator(project); p = intent(tools, ['ORPHAN'])
    tools.invoke(request('recover_ownership', p))
    successor = WorkTools(WorkPoise(project['config_path'], 'successor'))
    ctx = successor.invoke(request('bootstrap', {'task': {'id': 'ORPHAN'}, 'decision': None,
                                                'feedback': None, 'rework_stage': None}))
    edit(successor, 'ORPHAN', ctx['revision'], {'goal': 'New work after recovery'}, 'new-edit')
    before = rows(tools)
    recovered_operator = WorkTools(WorkPoise(project['config_path'], 'new-recovery-session'))
    assert recovered_operator.invoke(request('recover_ownership', p))['replayed'] is True
    assert rows(tools) == before


def test_worktree_only_orphan_recovery_does_not_change_the_task(project):
    old = seed_newborn(project)
    old.runtime.ownership.release_task('ORPHAN')
    old.runtime.ownership.acquire_worktree('ORPHAN')
    tools = operator(project)
    before = rows(tools)
    packet = intent(tools, ['ORPHAN'], 'worktree-only')
    out = tools.invoke(request('recover_ownership', packet))
    assert out['revoked'] == [
        {'actor': 'lost-planner', 'task_id': 'ORPHAN', 'resource': 'worktree'}]
    after = rows(tools)
    for table in ('tasks', 'task_events', 'task_execution', 'evidence',
                  'task_proofs', 'handoffs', 'action_runs'):
        assert after[table] == before[table]
    assert tools.runtime.ownership.snapshot('lost-planner').worktree_task_id is None
    with pytest.raises(PoiseError, match='revoked'):
        old.runtime.ownership.acquire_worktree('ORPHAN')
    tools.runtime.ownership.acquire_task('ORPHAN')
    tools.runtime.ownership.acquire_worktree('ORPHAN')
    after_acquire = rows(tools)
    assert tools.invoke(request('recover_ownership', packet))['replayed'] is True
    assert rows(tools) == after_acquire
