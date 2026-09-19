"""Duplicate admission through the real Task start path; all storage is test-owned."""
from copy import deepcopy
import json
from pathlib import Path

from conftest import WorkPoise
from sprints.helpers import setup, task


def available(project, runtime, identifier):
    body = task(project, identifier)
    body['sprint_id'] = None
    runtime.task_commands.create(
        body, runtime.session, runtime.processes['development'], [],
        {'config_hash': runtime.config_hash}, None, runtime.cfg.get('task_ids'),
        runtime._creation_base(), runtime.cfg['task_decomposition'],
    )


def related_pair(project):
    setup(project)
    planner = WorkPoise(project['config_path'], 'planner')
    available(project, planner, 'A')
    available(project, planner, 'B')
    # The test arranges a lineage row directly, without calling the reader under test.
    with planner.store.transaction() as db:
        db.execute("UPDATE tasks SET metadata=json_set(metadata,'$.duplicate_parent_id','A') WHERE id='B'")
    return planner


def test_second_equal_duplicate_does_not_start_or_create_worktree(project):
    planner = related_pair(project)
    first = WorkPoise(project['config_path'], 'one')
    assert first.bootstrap({'id': 'A'})['status'] == 'active'
    before = deepcopy(planner.task_queries.record('B'))
    second = WorkPoise(project['config_path'], 'two')
    result = second.bootstrap({'id': 'B'})
    assert result['status'] == 'duplicate_start_blocked'
    assert result['duplicate_gate']['reason'] == 'duplicate_sessions'
    assert result['duplicate_gate']['relatives'][0]['task_id'] == 'A'
    assert result['duplicate_gate']['relatives'][0]['session_id'] == 'one'
    assert planner.task_queries.record('B') == before
    assert second.current_task() is None
    with planner.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM task_execution WHERE task_id='B'").fetchone()[0] == 0


def test_force_is_explicit_per_request_and_does_not_bypass_ownership(project):
    from poise.application.work import WorkTools
    from batch.helpers import request
    related_pair(project)
    one=WorkPoise(project['config_path'],'one');two=WorkPoise(project['config_path'],'two')
    assert one.bootstrap({'id':'A'})['status']=='active'
    assert two.bootstrap({'id':'B'})['status']=='duplicate_start_blocked'
    forced=WorkTools(two).invoke(request('bootstrap',{
        'task':{'id':'B'},'decision':None,'feedback':None,'rework_stage':None,
        'force_duplicate_start':True}))
    assert forced['status']=='active' and forced['result_template'] is not None
    assert two.bootstrap()['duplicate_gate']['reason']=='duplicate_sessions'
    assert two.bootstrap(force_duplicate_start=True)['status']=='active'
    three=WorkPoise(project['config_path'],'three')
    assert three.bootstrap({'id':'B'},force_duplicate_start=True)['duplicate_gate']['reason']=='foreign_owner'


def test_force_does_not_bypass_completed_relative_and_writes_nothing(project):
    planner=related_pair(project)
    with planner.store.transaction() as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id='A'")
    before=deepcopy(planner.task_queries.record('B'))
    second=WorkPoise(project['config_path'],'two')
    response=second.bootstrap({'id':'B'},force_duplicate_start=True)
    assert response['duplicate_gate']['reason']=='family_ahead'
    assert planner.task_queries.record('B')==before


def test_start_rereads_after_preflight_and_creates_no_execution_if_relative_advanced(project):
    planner=related_pair(project)
    h=WorkPoise(project['config_path'],'two')
    original=h.task_commands.start
    def advance_before_write(*args,**kwargs):
        with planner.store.transaction() as db:
            db.execute("UPDATE tasks SET status='completed' WHERE id='A'")
        return original(*args,**kwargs)
    h.task_commands.start=advance_before_write
    assert h.bootstrap({'id':'B'},force_duplicate_start=True)['status']=='duplicate_start_blocked'
    with planner.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM task_execution WHERE task_id='B'").fetchone()[0]==0
    assert h.current_task() is None


def test_native_advance_requires_tie_consent_and_preserves_original_denial(project):
    from runtime_services.test_stage_progression import configure_progression
    from conftest import add_test
    from batch.helpers import result,verify
    from poise.application.work import WorkTools
    configure_progression(project)
    h=WorkPoise(project['config_path'],'one'); client=WorkTools(h)
    first=h.bootstrap(project['task'])
    add_test(first['worktree']);assert verify(client,result(first))['status']=='verified'
    # Existing, independently arranged sister at same current stage. Does not use
    # family reader or admission implementation to derive expected decisions.
    with h.store.transaction() as db:
        metadata=db.execute("SELECT metadata FROM tasks WHERE id='T1'").fetchone()[0]
        m=json.loads(metadata);m['duplicate_parent_id']='T1';m['contract']['id']='D'
        db.execute("INSERT INTO tasks VALUES('D','active',0,1,'two',0,NULL,?)",(json.dumps(m),))
    before=deepcopy(h.task_queries.record('T1'))
    outcome=h.advance('go','T1','implementation')
    assert outcome['status']=='duplicate_start_blocked'
    assert h.task_queries.record('T1')==before
    with h.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM journal WHERE event='progression.started'").fetchone()[0]==0
    allowed=h.advance('go','T1','implementation',force_duplicate_start=True)
    assert allowed['status']=='progression_target_reached' and allowed['stage']=='implementation'


def test_two_concurrent_preflights_admit_only_one_default_start(project):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    planner=related_pair(project)
    one=WorkPoise(project['config_path'],'one');two=WorkPoise(project['config_path'],'two')
    barrier=Barrier(2)
    def intercept(h):
        original=h.task_commands.start
        def start(*args,**kwargs):
            barrier.wait(timeout=5)
            return original(*args,**kwargs)
        h.task_commands.start=start
    intercept(one);intercept(two)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(one.bootstrap,{'id':'A'});b=pool.submit(two.bootstrap,{'id':'B'})
        statuses=sorted([a.result(timeout=15)['status'],b.result(timeout=15)['status']])
    assert statuses==['active','duplicate_start_blocked']
    with planner.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM task_execution').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM tasks WHERE claimed_by IS NOT NULL').fetchone()[0]==1


def test_handoff_resume_rechecks_family_and_force_is_narrow(project):
    from poise.application.work import WorkTools
    from batch.helpers import request
    planner=related_pair(project)
    one=WorkPoise(project['config_path'],'one')
    one.bootstrap({'id':'A'})
    WorkTools(one).invoke(request('handoff',{'request_id':'leave-A','reason':'Pass current Task',
        'result':None,'commit_message':None,'artifact_paths':[]}))
    two=WorkPoise(project['config_path'],'two');two.bootstrap({'id':'B'})
    three=WorkPoise(project['config_path'],'three')
    before=deepcopy(planner.task_queries.record('A'))
    refused=three.bootstrap({'id':'A'})
    assert refused['duplicate_gate']['reason']=='duplicate_sessions'
    assert planner.task_queries.record('A')==before
    resumed=three.bootstrap({'id':'A'},force_duplicate_start=True)
    assert resumed['status']=='active' and three.current_task()['claimed_by']=='three'


def test_automatic_creation_replay_preserves_explicit_tie_flag_in_context(project):
    setup(project)
    from conftest import write_json
    project['cfg']['task_ids']={'namespace':{'minimum':1,'maximum':9999},
        'width':4,'progression':{'first':1,'step':1}}
    write_json(project['config_path'],project['cfg'])
    first=WorkPoise(project['config_path'],'one')
    body=task(project,'unused');body.pop('id');body['sprint_id']=None
    intent={'request_id':'automatic-parent','task':body}
    context=first.bootstrap(intent)
    parent_id=context['task']
    planner=WorkPoise(project['config_path'],'planner')
    available(project,planner,'D')
    with planner.store.transaction() as db:
        db.execute("UPDATE tasks SET metadata=json_set(metadata,'$.duplicate_parent_id',?) WHERE id='D'",(parent_id,))
    second=WorkPoise(project['config_path'],'two')
    assert second.bootstrap({'id':'D'},force_duplicate_start=True)['status']=='active'
    repeated=first.bootstrap(intent,force_duplicate_start=True)
    assert repeated['duplicate_gate']['allowed'] is True
    assert repeated['result_template'] is not None
    assert first.bootstrap(intent)['status']=='duplicate_start_blocked'
