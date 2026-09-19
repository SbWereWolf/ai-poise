"""Recovery stays in the same Task/worktree; completed kin cannot trap local repair."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import WorkPoise, git
from batch.helpers import request
from sprints.helpers import verify
from runtime_services.test_duplicate_reuse import family_result, reuse
from runtime_services.test_task_restart import restart, immutable_audit_rows
from runtime_services.test_durable_check_attempts import fail_once
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError


def failing_reuse(project):
    h, source = family_result(project, command='from src.double import double; assert double(3) == 6')
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n + 1\n')
    git(project['app'], 'add', 'src/double.py')
    git(project['app'], 'commit', '-m', 'Later branch regression')
    c = WorkTools(h)
    failed = reuse(c)
    assert failed['status'] == 'checks_failed'
    return h, c, source, failed


def ready(h, c, newborn, name='ready-repair'):
    return c.invoke(request('task', {'action':'ready', 'task_id':'D',
        'expected_revision':newborn['revision'], 'request_id':name}))


def test_restart_failed_reuse_allows_local_fix_and_sprint_completion(project):
    h, c, source, failed = failing_reuse(project)
    before = h.task_queries.record('D')
    parent = deepcopy(h.task_queries.record('P'))
    old_rows = immutable_audit_rows(h, 'D')
    wt = Path(before['worktree'])
    (wt / 'notes.txt').write_text('Preserve untracked work')
    newborn = restart(c, 'D', before['version'])
    assert ready(h, c, newborn)['status'] == 'available'
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active', ctx
    assert ctx['worktree'] == str(wt)
    assert (wt/'notes.txt').read_text() == 'Preserve untracked work'
    (wt/'notes.txt').unlink()  # Test-owned scratch is not part of the task deliverable.
    (wt/'src/double.py').write_text('def double(n):\n    return n * 2\n')
    checked = verify(c, ctx)
    assert checked['status'] == 'verified', checked
    assert checked['checks'][0]['id'] != failed['checks'][0]['id']
    assert c.invoke(request('accept', {}))['status'] == 'completed'
    assert h.sprint_tools.overview('S')['status'] == 'completed'
    assert h.task_queries.record('P') == parent
    assert h.task_queries.record('D')['duplicate']['parent_id'] == 'P'
    new_rows = immutable_audit_rows(h, 'D')
    for table, rows in old_rows.items():
        assert new_rows[table][:len(rows)] == rows
    git(wt, 'merge-base', '--is-ancestor', source['commit'], 'HEAD')


def test_restart_after_changed_verified_reuse_preserves_dirty_work(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    reused = reuse(c)
    wt = Path(reused['worktree'])
    changed = 'def double(n):\n    return 2 * n  # local repair\n'
    (wt/'src/double.py').write_text(changed)
    with pytest.raises(PoiseError, match='clean|changed'):
        c.invoke(request('accept', {}))
    before = h.task_queries.record('D')
    newborn = restart(c, 'D', before['version'])
    ready(h, c, newborn)
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active', ctx
    assert (wt/'src/double.py').read_text() == changed
    assert verify(c, ctx)['status'] == 'verified'
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_restart_without_imported_result_does_not_bypass_family_gate(project):
    h, _ = family_result(project, integrate=False)
    c = WorkTools(h)
    before = h.task_queries.record('D')
    newborn = restart(c, 'D', before['version'])
    ready(h, c, newborn)
    out = h.bootstrap({'id':'D'}, force_duplicate_start=True)
    assert out['status'] == 'duplicate_start_blocked'
    assert out['duplicate_gate']['reason'] == 'family_ahead'


def test_restart_unknown_check_archives_attempt_without_replaying_command(project, monkeypatch):
    h, _ = family_result(project)
    c = WorkTools(h)
    calls = []
    run = h.check_runner.run
    def counted(*args, **kwargs):
        calls.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(h.check_runner, 'run', counted)
    fail_once(monkeypatch, h.evidence_commands, 'record_receipt')
    with pytest.raises(OSError, match='injected persistence'):
        reuse(c)
    before = h.task_queries.record('D')
    pending = deepcopy(before['pending'])
    assert len(calls) == 1
    newborn = restart(c, 'D', before['version'], authorization='Explicitly abandon uncertain check; inspect effects before fresh work.')
    assert len(calls) == 1
    after = h.task_queries.record('D')
    assert after['pending'] is None
    assert after['restart_history'][-1]['abandoned_check_attempt'] == pending
    ready(h,c,newborn)
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active', ctx
    assert verify(c,ctx)['status'] == 'verified'
    assert len(calls) == 2 and calls[0] != calls[1]
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_failed_reuse_reports_restart_recovery(project):
    h,c,_,failed = failing_reuse(project)
    assert failed['recovery']['action'] == 'restart'
    assert failed['recovery']['task_id'] == 'D'
    assert failed['recovery']['expected_version'] == h.task_queries.record('D')['version']


def test_restart_can_use_explicitly_imported_commit_without_prior_reuse(project):
    # The source need not be reimplemented/merged into main merely to fix this branch.
    h, source = family_result(project, integrate=False, start_target=True)
    c = WorkTools(h)
    before = h.task_queries.record('D')
    wt = Path(before['worktree'])
    git(wt, 'merge', '--ff-only', source['commit'])
    newborn = restart(c, 'D', before['version'])
    ready(h, c, newborn)
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active', ctx
    assert verify(c,ctx)['status'] == 'verified'
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_restart_refuses_lost_import_without_mutation(project):
    h,c,source,_ = failing_reuse(project)
    before = deepcopy(h.task_queries.record('D'))
    wt = Path(before['worktree'])
    git(wt,'reset','--hard',source['commit']+'^')  # Test-owned intentional invalidation.
    with pytest.raises(PoiseError,match='absent from local'):
        restart(c,'D',before['version'])
    assert h.task_queries.record('D') == before


def test_second_restart_and_fresh_session_preserve_repair_scope(project):
    h,c,_,_ = failing_reuse(project)
    first = restart(c,'D',h.task_queries.record('D')['version'])
    ready(h,c,first)
    h = WorkPoise(project['config_path'],'successor')
    c = WorkTools(h)
    assert h.bootstrap({'id':'D'})['status'] == 'active'
    second = restart(c,'D',h.task_queries.record('D')['version'],request_id='second-restart')
    ready(h,c,second,name='second-ready')
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active',ctx
    rows = h.task_queries.record('D')['restart_history']
    assert rows[-1]['local_repair']['source_commit'] == rows[-2]['local_repair']['source_commit']
    (Path(ctx['worktree'])/'src/double.py').write_text('def double(n):\n    return 2*n\n')
    assert verify(c,ctx)['status'] == 'verified'
    assert c.invoke(request('accept',{}))['status'] == 'completed'


def test_local_repair_handoff_and_foreign_owner_guard(project):
    h,c,_,_ = failing_reuse(project)
    ready(h,c,restart(c,'D',h.task_queries.record('D')['version']))
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active'
    other = WorkPoise(project['config_path'],'receiver')
    assert other.bootstrap({'id':'D'},force_duplicate_start=True)['duplicate_gate']['reason'] == 'foreign_owner'
    c.invoke(request('handoff',{'request_id':'repair-handoff','reason':'Continue local repair',
        'result':None,'commit_message':None,'artifact_paths':[]}))
    resumed = other.bootstrap({'id':'D'})
    assert resumed['status'] == 'active',resumed
    assert resumed['worktree'] == ctx['worktree']
    (Path(ctx['worktree'])/'src/double.py').write_text('def double(n):\n    return 2*n\n')
    assert verify(WorkTools(other),resumed)['status'] == 'verified'
    assert WorkTools(other).invoke(request('accept',{}))['status'] == 'completed'


def unknown_attempt(project,monkeypatch):
    h,_ = family_result(project)
    c = WorkTools(h)
    fail_once(monkeypatch,h.evidence_commands,'record_receipt')
    with pytest.raises(OSError,match='injected persistence'):
        reuse(c)
    return h,c,deepcopy(h.task_queries.record('D'))


def test_unknown_restart_refuses_running_check_without_state_changes(project,monkeypatch):
    h,c,before = unknown_attempt(project,monkeypatch)
    monkeypatch.setattr(h.check_runner,'active_ids',lambda:(before['pending']['runs'][0]['run_id'],))
    with pytest.raises(PoiseError,match='still running'):
        restart(c,'D',before['version'])
    assert h.task_queries.record('D') == before


def test_unknown_restart_archival_rolls_back_on_failure(project,monkeypatch):
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
    h,c,before = unknown_attempt(project,monkeypatch)
    fail_once(monkeypatch,SqliteTaskRepository,'restart_newborn')
    with pytest.raises(OSError,match='injected persistence'):
        restart(c,'D',before['version'])
    assert h.task_queries.record('D') == before
    assert restart(c,'D',before['version'])['status'] == 'newborn'


def test_unknown_restart_replay_is_read_only_after_ready(project,monkeypatch):
    h,c,before = unknown_attempt(project,monkeypatch)
    newborn = restart(c,'D',before['version'])
    ready(h,c,newborn)
    after = deepcopy(h.task_queries.record('D'))
    replay = restart(c,'D',before['version'])
    assert replay['replayed'] is True
    assert h.task_queries.record('D') == after
    assert after['restart_history'][-1]['abandoned_check_attempt'] == before['pending']


def test_failed_local_check_rework_can_finish_same_sprint(project):
    h,c,_,_ = failing_reuse(project)
    ready(h,c,restart(c,'D',h.task_queries.record('D')['version']))
    ctx = h.bootstrap({'id':'D'})
    failed = verify(c,ctx)
    assert failed['status'] != 'verified'
    with pytest.raises(PoiseError):c.invoke(request('accept',{}))
    repaired = h.bootstrap(decision='rework',feedback='Repair branch-local requirement',rework_stage='work')
    assert repaired['status'] == 'active',repaired
    (Path(repaired['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    assert verify(c,repaired)['status'] == 'verified'
    assert c.invoke(request('accept',{}))['status'] == 'completed'
    assert h.sprint_tools.overview('S')['status'] == 'completed'


def test_restart_before_first_worktree_uses_imported_main_input(project):
    h,source = family_result(project)
    c = WorkTools(h)
    before = h.task_queries.record('D')
    assert before['worktree'] is None
    newborn = restart(c,'D',before['version'])
    assert h.task_queries.record('D')['worktree'] is None
    ready(h,c,newborn)
    ctx = h.bootstrap({'id':'D'})
    assert ctx['status'] == 'active',ctx
    git(Path(ctx['worktree']),'merge-base','--is-ancestor',source['commit'],'HEAD')
    assert verify(c,ctx)['status'] == 'verified'
    assert c.invoke(request('accept',{}))['status'] == 'completed'


def test_reserved_base_must_still_contain_import_after_restart(project):
    h,source = family_result(project)
    c = WorkTools(h)
    newborn = restart(c,'D',h.task_queries.record('D')['version'])
    ready(h,c,newborn)
    before = deepcopy(h.task_queries.record('D'))
    git(project['app'],'reset','--hard',source['commit']+'^')  # Test-only base drift.
    with pytest.raises(PoiseError,match='absent from reserved base'):
        h.bootstrap({'id':'D'})
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_completed_family_block_supplies_commit_and_recovery_route(project):
    h,source = family_result(project)
    blocked = h.bootstrap({'id':'D'})
    assert blocked['status'] == 'duplicate_start_blocked'
    assert blocked['duplicate_gate']['relatives'][0]['result_commit'] == source['commit']
    assert blocked['recovery']['action'] == 'import_then_verify'
    assert blocked['recovery']['on_verification_failure'] == 'restart'
    assert 'reuse' in blocked['next_work'] and 'restart' in blocked['next_work']


def test_repaired_duplicate_unlocks_real_result_dependent(project):
    from tasks.test_duplicate_creation import prepared,duplicate
    from sprints.helpers import task,draft,publish
    planner,pc = prepared(project,command='from src.double import double; assert double(3)==6')
    duplicate(pc)
    planner.bootstrap({'id':'D'})
    d=planner.task_queries.record('D')
    planner.task_action({'action':'ready','task_id':'D','expected_revision':d['revision'],
                         'request_id':'ready-D'})
    draft(pc,[],revision=planner.sprint_tools.overview('S')['revision'],
          request_id='dependent',updates=[
              {'kind':'upsert_tasks','tasks':[task(project,'NEXT')]},
              {'kind':'dependencies','items':[{'predecessor':'D','successor':'NEXT','kind':'result'}]}])
    publish(pc,planner.sprint_tools.overview('S')['revision'])
    producer=WorkPoise(project['config_path'],'producer');p=producer.bootstrap({'id':'P'})
    (Path(p['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    source=verify(WorkTools(producer),p)
    assert WorkTools(producer).invoke(request('accept',{}))['status']=='completed'
    git(project['app'],'merge','--ff-only',source['commit'])
    (project['app']/'src/double.py').write_text('def double(n):\n    return n+1\n')
    git(project['app'],'add','src/double.py');git(project['app'],'commit','-m','Branch-specific regression')
    h=WorkPoise(project['config_path'],'consumer');c=WorkTools(h)
    assert reuse(c)['status']=='checks_failed'
    ready(h,c,restart(c,'D',h.task_queries.record('D')['version']))
    current=h.bootstrap({'id':'D'});assert current['status']=='active'
    assert h.sprint_tools.overview('S')['status']!='completed'
    (Path(current['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    repaired=verify(c,current);assert repaired['status']=='verified'
    assert c.invoke(request('accept',{}))['status']=='completed'
    assert h.sprint_tools.overview('S')['status']!='completed'
    git(project['app'],'merge','--ff-only',repaired['commit'])
    next_ctx=h.bootstrap({'id':'NEXT'});assert next_ctx['status']=='active',next_ctx
    assert verify(c,next_ctx)['status']=='verified'
    assert c.invoke(request('accept',{}))['status']=='completed'
    assert h.sprint_tools.overview('S')['status']=='completed'


def test_reviewed_check_revision_after_restart_uses_new_local_evidence(project):
    """An explicit reviewer decision is not an automatic weakening on failure."""
    h, c, _, failed = failing_reuse(project)
    parent = deepcopy(h.task_queries.record('P'))
    before = h.task_queries.record('D')
    newborn = restart(c, 'D', before['version'],
        reason='Reviewer changed the branch-specific verification requirement.',
        authorization='Reviewer explicitly approved double(3)==4 for this branch.')
    methods = deepcopy(h.task_queries.record('D')['draft']['methods'])
    methods[0]['argv'][-1] = 'from src.double import double; assert double(3) == 4'
    edited = c.invoke(request('task', {
        'action': 'edit', 'task_id': 'D', 'expected_revision': newborn['revision'],
        'request_id': 'reviewed-check-revision', 'patch': {'methods': methods}, 'remove': [],
    }))
    ready(h, c, edited)
    ctx = h.bootstrap({'id': 'D'})
    assert ctx['status'] == 'active'
    checked = verify(c, ctx)
    assert checked['status'] == 'verified', checked
    assert checked['checks'][0]['id'] != failed['checks'][0]['id']
    assert checked['checks'][0]['passed'] is True
    assert c.invoke(request('accept', {}))['status'] == 'completed'
    assert h.task_queries.record('P') == parent
    assert h.sprint_tools.overview('S')['status'] == 'completed'
