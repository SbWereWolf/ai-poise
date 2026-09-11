import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from copy import deepcopy
import pytest
from conftest import WorkPoise as Poise
from poise.common import PoiseError
from poise.application.work import WorkTools
from poise.interfaces.work import execute
from batch.helpers import bootstrap,result,verify,request
from conftest import add_test,write_json,git
from .test_adapter import settings,packet,boot,logline
from .test_handoff import transfer


def test_real_verify_result_views_can_be_read_after_runtime_cleanup(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree'])
    out=verify(t,result(c))
    assert out['status']=='verified' and not h.runtime.exists()
    rec=out['checks'][0]
    manifest=json.loads(Path(rec['presentation']['manifest']).read_text())
    assert manifest['status']=='ready'
    read=t.invoke(request('show',{'queries':[{'id':'o','kind':'tool_result','receipt_id':rec['id'],'representation':'full','range':None}]}))
    assert 'test_double' in read['results'][0]['value']['text']
    with pytest.raises(PoiseError):t.invoke(request('show',{'queries':[{'id':'x','kind':'tool_result','receipt_id':'foreign','representation':'full','range':None}]}))


def test_failed_check_still_materializes_and_successful_retry_new_result(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project)
    wt=Path(c['worktree']);(wt/'tests').mkdir();(wt/'tests/test_double.py').write_text('import missing_module\n')
    p=result(c);bad=verify(t,p)
    assert bad['status']=='checks_failed'
    assert all(json.loads(Path(r['presentation']['manifest']).read_text())['status']=='ready' for r in bad['checks'])
    add_test(c['worktree']);good=verify(t,p)
    assert good['status']=='verified' and good['checks'][0]['id']!=bad['checks'][0]['id']


def test_handoff_release_transaction_rolls_back_with_receipt_available_for_retry(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree']);p=result(c)
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_handoff BEFORE UPDATE ON handoffs BEGIN SELECT RAISE(ABORT,'test-fault'); END")
    with pytest.raises(sqlite3.IntegrityError,match='test-fault'):transfer(t,p)
    assert h.current_task()['claimed_by']=='A'
    commit=git(Path(c['worktree']),'rev-parse','HEAD')
    with h.store.transaction() as db:db.execute('DROP TRIGGER fail_handoff')
    out=transfer(t,p)
    assert out['commit']==commit and h.current_task() is None


def test_handoff_replay_does_not_clear_new_task_binding(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree']);p=result(c)
    first=transfer(t,p)
    other=deepcopy(project['task']);other['id']='T2'
    t.invoke(request('bootstrap',{'task':other,'decision':None,'feedback':None,'rework_stage':None}))
    again=transfer(t,p)
    assert again['commit']==first['commit'] and again['replayed']
    assert h.current_task()['id']=='T2'


def test_cli_handoff_keeps_returned_response_outside_deleted_runtime(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree']);p=result(c)
    inp=request('handoff',{'request_id':'cli','reason':'switch actor','result':p,'commit_message':'WIP: cli','artifact_paths':[]})
    out=io.StringIO();assert execute(h,io.BytesIO(json.dumps(inp).encode()),out)==0
    reply=json.loads(out.getvalue())
    assert Path(reply['response_path']).is_file()
    assert not h.runtime.exists()


def test_runtime_cli_without_poise_session_env_and_real_transcript(project,tmp_path):
    project['cfg']['batch']['message_source']={'id':'codex-local','mode':'runtime_event'}
    write_json(project['config_path'],project['cfg']);log=tmp_path/'log.jsonl';log.write_bytes(logline('user_message'))
    cfg=write_json(tmp_path/'adapter.json',settings(project,tmp_path))
    env={k:v for k,v in os.environ.items() if k not in ('POISE_SESSION','POISE_CONFIG')}
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[2]/'src')
    r=subprocess.run([sys.executable,'-m','poise','runtime','--settings',str(cfg)],
       input=json.dumps(packet(boot(project),transcript={'path':str(log),'initial_offset':0})),
       text=True,capture_output=True,env=env,timeout=20)
    assert r.returncode==0,r.stderr
    reply=json.loads(r.stdout)
    full=json.loads(Path(reply['response_path']).read_text()) if 'response_path' in reply else reply
    assert full['interaction']['observed_messages_count']==1
    assert full['runtime_adapter']['session_id']


def test_message_cost_recorded_even_when_required_capability_blocks_current_task(project,tmp_path):
    from poise.infrastructure.runtime_adapter import RuntimeAdapter
    project['cfg']['batch']['message_source']={'id':'codex-local','mode':'runtime_event'};write_json(project['config_path'],project['cfg'])
    log=tmp_path/'log';log.write_bytes(logline('user_message'))
    cfg=settings(project,tmp_path);a=RuntimeAdapter(cfg)
    p=packet(boot(project),transcript={'path':str(log),'initial_offset':0});a.invoke(p)
    with log.open('ab') as f:f.write(logline('user_message','please continue'))
    cfg['required_capabilities']=['rename'];b=RuntimeAdapter(cfg)
    p['work']=request('show',{'queries':[{'id':'m','kind':'messages'}]})
    with pytest.raises(PoiseError):b.invoke(p)
    assert a.runtime.interactions.summary(a.runtime.current_task())['observed_messages_count']==2


def test_old_schema_not_read_as_current(project):
    project['cfg']['schema']='ddd-actions-8';write_json(project['config_path'],project['cfg'])
    with pytest.raises(PoiseError):Poise(project['config_path'],'x')


def test_parser_incident_is_preserved_in_verified_report_after_restart(project,monkeypatch):
    import poise.infrastructure.result_views as output_module
    real=output_module.subprocess.Popen
    def launch(argv,*args,**kwargs):
        if 'poise.output_worker' in argv:raise OSError('injected parser worker launch failure')
        return real(argv,*args,**kwargs)
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree'])
    monkeypatch.setattr(output_module.subprocess,'Popen',launch)
    out=verify(t,result(c))
    assert out['status']=='verified' and out['checks'][0]['passed'] and out['incidents']
    replay=WorkTools(Poise(project['config_path'],'A')).invoke(request('verify',{'result':None,'artifacts':[]}))
    assert replay['incidents']==out['incidents']


def test_handoff_automatically_preserves_registered_runtime_paths(project):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project)
    paths=t.invoke(request('artifacts',{'items':[{'scope':'runtime','path':'notes.txt','source':{'kind':'text','text':'Keep already registered work'}}]}))['artifact_paths']
    p=result(c);p['artifact_paths']=paths;h.runner.submit('T1','A',p)
    out=transfer(t,None,message_=None)
    assert out['preserved_artifacts'] and not h.runtime.exists()
    b=WorkTools(Poise(project['config_path'],'B'))
    next_=b.invoke(request('bootstrap',{'task':{'id':'T1'},'decision':None,'feedback':None,'rework_stage':None}))
    assert all(Path(x).is_file() for x in next_['result_template']['artifact_paths'])


def test_view_storage_failure_does_not_erase_command_evidence(project,monkeypatch):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree'])
    original=Path.mkdir
    def denied(self,*a,**kw):
        if self.name==h.cfg['runtime_services']['output']['directory']:raise OSError('views directory unavailable')
        return original(self,*a,**kw)
    monkeypatch.setattr(Path,'mkdir',denied)
    out=verify(t,result(c))
    assert out['status']=='verified' and out['checks'][0]['passed'] and out['incidents']
    assert h.store.counts('T1')[1]==1


def test_large_result_read_does_not_load_whole_file(project,monkeypatch):
    h=Poise(project['config_path'],'A');t=WorkTools(h);c=bootstrap(t,project);add_test(c['worktree']);out=verify(t,result(c))
    r=out['checks'][0];manifest=json.loads(Path(r['presentation']['manifest']).read_text());full=Path(manifest['representations']['full']['path'])
    original=Path.read_text
    def no_full_read(self,*a,**kw):
        if self==full:raise AssertionError('selected range must not load full output')
        return original(self,*a,**kw)
    monkeypatch.setattr(Path,'read_text',no_full_read)
    res=t.invoke(request('show',{'queries':[{'id':'first','kind':'tool_result','receipt_id':r['id'],'representation':'full','range':{'unit':'lines','start':1,'end':1}}]}))
    assert res['results'][0]['value']['returned_lines']==[1,1]


def test_sprint_exposes_explicit_handoff_as_resumable_without_rebasing(project):
    from sprints.helpers import setup,task,draft,publish,bootstrap as select
    setup(project)
    a=WorkTools(Poise(project['config_path'],'A'))
    d=draft(a,[task(project)]);publish(a,d['revision']);c=select(a,'A')
    Path(c['worktree'],'src/double.py').write_text('def double(n):\n    return n * 7\n')
    p=deepcopy(c['result_template']);p['sections']['report']='Unfinished work preserved'
    transfer(a,p)
    b=WorkTools(Poise(project['config_path'],'B'))
    overview=select(b,'S')
    assert overview['eligible']==['A'] and overview['active']==[]
    assert overview['resumable']==['A']
    resumed=select(b,'A')
    assert resumed['worktree']==c['worktree'] and resumed['iteration']==c['iteration']
    assert Path(resumed['worktree'],'src/double.py').read_text().endswith('return n * 7\n')
    assert b.runtime.sprint_tools.overview('S')['resumable']==[]
