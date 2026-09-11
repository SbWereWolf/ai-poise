from pathlib import Path
from copy import deepcopy
import pytest
from harness.common import HarnessError
from conftest import WorkHarness as Harness
from harness.application.work import WorkTools
from harness.application.handoff import HandoffCommands
from batch.helpers import bootstrap,result,request,verify,message
from conftest import add_test,git


def transfer(tools, payload=None, id='move1',message_='WIP: handoff'):
    return tools.invoke(request('handoff',{'request_id':id,'reason':'continue with another agent',
                       'result':payload,'commit_message':message_,'artifact_paths':[]}))


def test_wip_handoff_is_durable_and_other_actor_resumes_same_iteration(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project)
    add_test(c['worktree']);draft=result(c,'Saved incomplete work')
    out=transfer(a,draft)
    assert out['status']=='handed_off' and out['verified'] is False
    assert h.current_task() is None
    saved=h.task_queries.record('T1');assert saved['claimed_by'] is None and saved['status']=='active'
    assert git(Path(c['worktree']),'rev-parse','HEAD')==out['commit']
    assert Path(out['receipt_path']).is_file() and Path(out['bundle_path']).is_file()
    b=WorkTools(Harness(project['config_path'],'B'))
    nxt=b.invoke(request('bootstrap',{'task':{'id':'T1'},'decision':None,'feedback':None,'rework_stage':None}))
    assert nxt['stage']==c['stage'] and nxt['iteration']==c['iteration']
    assert nxt['result_template']['sections']['report']=='Saved incomplete work'
    assert nxt['worktree']==c['worktree']
    assert verify(b,nxt['result_template'])['status']=='verified'


def test_verified_handoff_preserves_receipt_without_new_commit_or_tests(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project);add_test(c['worktree'])
    done=verify(a,result(c));counts=h.store.counts('T1')
    out=transfer(a,None,message_=None)
    assert out['verified'] and out['commit']==done['commit']
    b=WorkTools(Harness(project['config_path'],'B'))
    nxt=b.invoke(request('bootstrap',{'task':{'id':'T1'},'decision':None,'feedback':None,'rework_stage':None}))
    assert nxt['status']=='verified'
    assert b.runtime.store.counts('T1')==counts


def test_handoff_replay_does_not_release_a_different_current_task(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project);add_test(c['worktree'])
    out=transfer(a,result(c))
    assert transfer(a,result(c))=={**out,'replayed':True}
    assert h.task_queries.record('T1')['claimed_by'] is None


def test_changed_handoff_intent_rejected(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project);add_test(c['worktree']);transfer(a,result(c))
    with pytest.raises(HarnessError):transfer(a,result(c,'different'))


def test_pending_command_blocks_release(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project)
    data=h.current_task();data['pending']='checks';h.store.save(data)
    with pytest.raises(HarnessError):transfer(a,result(c))
    assert h.current_task()['claimed_by']=='A'


def test_resume_does_not_adopt_changed_worktree(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project);add_test(c['worktree']);transfer(a,result(c))
    (Path(c['worktree'])/'src/double.py').write_text('external change')
    b=WorkTools(Harness(project['config_path'],'B'))
    with pytest.raises(HarnessError):b.invoke(request('bootstrap',{'task':{'id':'T1'},'decision':None,'feedback':None,'rework_stage':None}))
    assert h.task_queries.record('T1')['claimed_by'] is None


def test_selected_runtime_file_promoted_before_cleanup(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);c=bootstrap(a,project)
    paths=a.invoke(request('artifacts',{'items':[{'scope':'runtime','path':'note.txt','source':{'kind':'text','text':'Resume this'}}]}))['artifact_paths']
    out=a.invoke(request('handoff',{'request_id':'x','reason':'handoff','result':None,'commit_message':None,'artifact_paths':paths}))
    assert not h.runtime.exists()
    assert len(out['preserved_artifacts'])==1 and Path(out['preserved_artifacts'][0]).read_text()=='Resume this'


def test_new_task_cannot_be_started_without_handoff(project):
    h=Harness(project['config_path'],'A');a=WorkTools(h);bootstrap(a,project)
    other=deepcopy(project['task']);other['id']='T2'
    with pytest.raises(HarnessError):a.invoke(request('bootstrap',{'task':other,'decision':None,'feedback':None,'rework_stage':None}))
