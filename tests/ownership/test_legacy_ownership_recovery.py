"""An ambiguous legacy owner blocks its component, never the whole project."""
from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.ownership.domain import Liveness
from poise.infrastructure.sqlite.ownership import SqliteOwnershipRepository


def show_conflicts(tools):
    return tools.invoke(request('show',{'queries':[{'id':'legacy','kind':'ownership_conflicts'}]}))['results'][0]['value']


def rows(runtime):
    with runtime.store.transaction() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n:[tuple(r) for r in db.execute(f'SELECT * FROM "{n}" ORDER BY rowid')] for n in names}


def files(path):
    return {p.relative_to(path).as_posix():p.read_bytes() for p in Path(path).rglob('*') if p.is_file() and '.git' not in p.parts}


@pytest.fixture
def legacy(project):
    configure(project)
    locations=[]
    for task_id,actor in [('0028','old-seed'),('0124','good-seed')]:
        runtime=WorkPoise(project['config_path'],actor)
        task=deepcopy(project['task']);task['id']=task_id
        ctx=runtime.bootstrap(task)
        locations.append(Path(ctx['worktree']))
        runtime.ownership.release_task(task_id)
    for p in locations:
        (p/'untracked-wip.txt').write_text('Do not delete or overwrite WIP')
    with runtime.store.transaction() as db:
        db.execute('DROP INDEX tasks_single_claimant')
        db.execute('DROP INDEX sessions_single_worktree_owner')
        db.execute("INSERT INTO sessions VALUES('legacy-a','0028')")
        db.execute("INSERT INTO sessions VALUES('legacy-b','0028')")
        db.execute('PRAGMA user_version=12')
    return project,locations,runtime.store.database.path


def client(legacy,liveness=Liveness.DEAD):
    project,_,_=legacy
    h=WorkPoise(project['config_path'],'migration-operator')
    h.ownership.commands.liveness=lambda _:liveness
    return WorkTools(h)


def decision(tools,request_id='repair-0028'):
    component=show_conflicts(tools)['conflicts'][0]
    return {'request_id':request_id,'task_ids':component['task_ids'],
            'expected_snapshot':component['expected_snapshot'],
            'task_claims':{r['id']:r['claimed_by'] for r in component['tasks']},
            'worktree_bindings':{r['id']:None if r['id']=='legacy-b' else r['task_id'] for r in component['sessions']},
            'reason':'Remove an explicitly selected obsolete worktree binding',
            'authorization':'User authorized this exact ownership repair'}


def test_unrelated_public_bootstrap_and_show_survive_ambiguous_legacy_owner(legacy):
    tools=client(legacy);p,locations,_=legacy;before=[files(x) for x in locations]
    overview=show_conflicts(tools)
    assert overview['schema_version']==12
    assert overview['status']=='ownership_migration_pending'
    assert overview['conflicts'][0]['task_ids']==['0028']
    ctx=tools.invoke(request('bootstrap',{'task':{'id':'0124'},'decision':None,'feedback':None,'rework_stage':None}))
    assert ctx['task']=='0124'
    assert tools.invoke(request('show',{'queries':[{'id':'task','kind':'task'}]}))['status']=='read_only'
    assert [files(x) for x in locations]==before
    with pytest.raises(PoiseError,match='recover_ownership'):
        tools.runtime.task_commands.workflow_context('0028')


def test_explicit_recovery_is_atomic_replayable_and_finishes_upgrade(legacy):
    tools=client(legacy);_,locations,_=legacy;before=[files(x) for x in locations]
    packet=decision(tools)
    result=tools.invoke(request('recover_ownership',packet))
    assert result['status']=='ownership_reconciled' and result['schema_version']==13
    assert show_conflicts(tools)['conflicts']==[]
    assert [files(x) for x in locations]==before
    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT task_id FROM sessions WHERE id='legacy-a'").fetchone()[0]=='0028'
        assert db.execute("SELECT task_id FROM sessions WHERE id='legacy-b'").fetchone()[0] is None
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name IN ('tasks_single_claimant','sessions_single_worktree_owner')").fetchone()[0]==2
    snapshot=rows(tools.runtime)
    assert tools.invoke(request('recover_ownership',packet))['replayed'] is True
    assert rows(tools.runtime)==snapshot
    conflict=deepcopy(packet);conflict['worktree_bindings']['legacy-a']=None
    with pytest.raises(PoiseError,match='conflict'):
        tools.invoke(request('recover_ownership',conflict))
    assert rows(tools.runtime)==snapshot


@pytest.mark.parametrize('state',[Liveness.LIVE,Liveness.UNCERTAIN])
def test_live_or_uncertain_owner_is_never_stolen(legacy,state):
    tools=client(legacy,state);packet=decision(tools);before=rows(tools.runtime)
    with pytest.raises(PoiseError,match='uncertain live'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


def test_unchanged_retained_live_owner_needs_no_release(legacy):
    tools=client(legacy)
    tools.runtime.ownership.commands.liveness=lambda s:Liveness.LIVE if s=='legacy-a' else Liveness.DEAD
    assert tools.invoke(request('recover_ownership',decision(tools)))['status']=='ownership_reconciled'


def test_changed_snapshot_rejects_without_any_mutation(legacy):
    tools=client(legacy);packet=decision(tools)
    with tools.runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET version=version+1 WHERE id='0028'")
    before=rows(tools.runtime)
    with pytest.raises(PoiseError,match='snapshot'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


@pytest.mark.parametrize('bad',['missing_session','new_owner','unresolved','unknown_task','unrelated_binding'])
def test_recovery_requires_exact_closed_component_decisions(legacy,bad):
    tools=client(legacy);packet=decision(tools);before=rows(tools.runtime)
    if bad=='missing_session':packet['worktree_bindings'].pop('legacy-b')
    if bad=='new_owner':packet['task_claims']['0028']='invented-owner'
    if bad=='unresolved':packet['worktree_bindings']['legacy-b']='0028'
    if bad=='unknown_task':packet['task_ids'].append('unknown')
    if bad=='unrelated_binding':packet['worktree_bindings']['legacy-a']='0124'
    with pytest.raises(PoiseError):tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


def test_failure_after_ownership_changes_rolls_back_everything(legacy,monkeypatch):
    tools=client(legacy);packet=decision(tools);before=rows(tools.runtime)
    original=SqliteOwnershipRepository.reconcile_legacy
    def after_write(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise PoiseError('injected after ownership write')
    monkeypatch.setattr(SqliteOwnershipRepository,'reconcile_legacy',after_write)
    with pytest.raises(PoiseError,match='injected'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before
    with tools.runtime.store.transaction() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==12


def test_one_pending_component_does_not_block_repairing_another(legacy):
    tools=client(legacy)
    with tools.runtime.store.transaction() as db:
        db.execute("INSERT INTO sessions VALUES('other-a','0124')")
        db.execute("INSERT INTO sessions VALUES('other-b','0124')")
    before=show_conflicts(tools);assert len(before['conflicts'])==2
    repaired=tools.invoke(request('recover_ownership',decision(tools)))
    assert repaired['schema_version']==12 and repaired['remaining_conflicts']==1
    assert show_conflicts(tools)['conflicts'][0]['task_ids']==['0124']


@pytest.mark.parametrize('binding_count',[0,1,2])
def test_completed_unclaimed_task_has_no_migrated_worktree_owner(legacy,binding_count):
    _,locations,path=legacy;before=[files(p) for p in locations]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id='0028'")
        db.execute("DELETE FROM sessions WHERE task_id='0028'")
        db.executemany("INSERT INTO sessions VALUES(?,'0028')",[(f'stale-{n}',) for n in range(binding_count)])
    tools=client(legacy)
    assert show_conflicts(tools)['schema_version']==13
    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE task_id='0028'").fetchone()[0]==0
        assert db.execute("SELECT status,claimed_by FROM tasks WHERE id='0028'").fetchone()[:]==('completed',None)
    assert [files(p) for p in locations]==before


def duplicate_task_claims(legacy):
    _,_,path=legacy
    with sqlite3.connect(path) as db:
        db.execute("UPDATE tasks SET claimed_by='legacy-a' WHERE id IN ('0028','0124')")


def test_duplicate_task_claims_need_explicit_decision_not_worktree_inference(legacy):
    duplicate_task_claims(legacy)
    tools=client(legacy)
    component=show_conflicts(tools)['conflicts'][0]
    assert component['task_ids']==['0028','0124']
    assert {r['claimed_by'] for r in component['tasks']}=={'legacy-a'}
    packet=decision(tools)
    packet['task_claims']['0124']=None
    before=rows(tools.runtime)
    result=tools.invoke(request('recover_ownership',packet))
    assert result['schema_version']==13
    after=rows(tools.runtime)
    for table in before:
        if table not in {'tasks','sessions','journal','task_events'}:
            assert after[table]==before[table],table
    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT claimed_by,version FROM tasks WHERE id='0028'").fetchone()[0]=='legacy-a'
        assert db.execute("SELECT claimed_by FROM tasks WHERE id='0124'").fetchone()[0] is None
        released=json.loads(db.execute("SELECT data FROM task_events WHERE task_id='0124' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        assert released['event']=='ownership_released'
        assert released['recovery']=='legacy_ownership'
        assert released['previous_owner']=='legacy-a'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_explicit_self_release_works_for_a_conflicted_native_session(legacy):
    duplicate_task_claims(legacy)
    project,_,_=legacy
    runtime=WorkPoise(project['config_path'],'legacy-a')
    def liveness(actor):
        assert actor!='legacy-a','Self-release must not be treated as stealing a foreign owner'
        return Liveness.DEAD
    runtime.ownership.commands.liveness=liveness
    tools=WorkTools(runtime)
    packet=decision(tools)
    packet['task_claims']['0124']=None
    assert tools.invoke(request('recover_ownership',packet))['schema_version']==13
    snapshot=rows(runtime)
    assert tools.invoke(request('recover_ownership',packet))['replayed'] is True
    assert rows(runtime)==snapshot


def test_live_duplicate_task_claim_cannot_be_selected_from_binding(legacy):
    duplicate_task_claims(legacy)
    tools=client(legacy)
    tools.runtime.ownership.commands.liveness=lambda actor:Liveness.LIVE if actor=='legacy-a' else Liveness.DEAD
    packet=decision(tools);packet['task_claims']['0124']=None
    before=rows(tools.runtime)
    with pytest.raises(PoiseError,match='uncertain live'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


def test_session_change_invalidates_component_snapshot(legacy):
    tools=client(legacy);packet=decision(tools)
    with tools.runtime.store.transaction() as db:
        db.execute("INSERT INTO sessions VALUES('third','0028')")
    before=rows(tools.runtime)
    with pytest.raises(PoiseError,match='snapshot'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


def test_repair_preserves_foreign_committed_staged_unstaged_and_untracked_wip(legacy):
    import subprocess
    import os
    _,locations,_=legacy
    def git(path,*args):
        return subprocess.check_output(['git','-C',str(path),*args],env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
    path=locations[0]
    (path/'committed.txt').write_text('committed foreign work')
    git(path,'add','committed.txt');git(path,'commit','-m','Preserved foreign work')
    (path/'staged.txt').write_text('staged foreign work')
    git(path,'add','staged.txt')
    (path/'committed.txt').write_text('unstaged foreign edit')
    def snapshot():
        return [(files(p),git(p,'rev-parse','HEAD'),git(p,'status','--porcelain=v1'),
                 git(p,'diff','--binary'),git(p,'diff','--cached','--binary')) for p in locations]
    before=snapshot()
    tools=client(legacy)
    tools.invoke(request('recover_ownership',decision(tools)))
    assert snapshot()==before


@pytest.mark.parametrize('field,value',[
    ('request_id',''),('authorization',' '),('reason',None),
    ('expected_snapshot','not-a-digest'),('task_ids',['0028','0028']),
    ('task_ids','0028'),('task_claims',{'0028':False}),
    ('worktree_bindings',{'legacy-a':''}),
])
def test_malformed_repair_rejects_before_mutation(legacy,field,value):
    tools=client(legacy);packet=decision(tools);packet[field]=value
    before=rows(tools.runtime)
    with pytest.raises(PoiseError):tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before


def test_duplicate_claim_repair_keeps_ownership_suffix_resumable(legacy):
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
    duplicate_task_claims(legacy)
    tools=client(legacy);packet=decision(tools);packet['task_claims']['0124']=None
    before=next(t['version'] for t in show_conflicts(tools)['conflicts'][0]['tasks'] if t['id']=='0124')
    tools.invoke(request('recover_ownership',packet))
    with tools.runtime.store.transaction() as db:
        assert SqliteTaskRepository(db).ownership_event_suffix('0124',before,before+1)==('ownership_released',)


def test_duplicate_claim_repair_rollback_preserves_versions_events_and_bindings(legacy,monkeypatch):
    duplicate_task_claims(legacy)
    tools=client(legacy);packet=decision(tools);packet['task_claims']['0124']=None
    before=rows(tools.runtime)
    original=SqliteOwnershipRepository.reconcile_legacy
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise PoiseError('failure after the entire component is written')
    monkeypatch.setattr(SqliteOwnershipRepository,'reconcile_legacy',fail)
    with pytest.raises(PoiseError,match='entire component'):
        tools.invoke(request('recover_ownership',packet))
    assert rows(tools.runtime)==before
    with tools.runtime.store.transaction() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==12
