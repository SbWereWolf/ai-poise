"""Explicit runtime surfaces; not simulated knowledge of hidden ChatGPT tools."""
import json
from pathlib import Path
from copy import deepcopy
import pytest
from harness.common import HarnessError
from harness.infrastructure.runtime_adapter import RuntimeAdapter, CodexTranscript
from harness.modules.runtime_adapter.domain import RuntimeIdentity, validate_inventory
from batch.helpers import request
from conftest import write_json


def settings(project,tmp_path):
    return {'schema':'runtime-adapter-1','project_config':str(project['config_path']),
            'adapter_id':'codex-local','transcript_roots':[str(tmp_path)],
            'max_scan_bytes':1048576,'max_line_bytes':65536,'max_events':100,
            'required_capabilities':[]}


def packet(work,session='chat1',agent='main',transcript=None,capabilities=()):
    return {'identity':{'kind':'external','session_id':session,'agent_id':agent},
            'capabilities':list(capabilities),'transcript':transcript,'work':work}


def boot(project):
    return request('bootstrap',{'task':deepcopy(project['task']),'decision':None,'feedback':None,'rework_stage':None})


def logline(kind,text='hello'):
    return json.dumps({'timestamp':'2026-09-06T18:00:00Z','type':'event_msg','payload':{'type':kind,'message':text}},ensure_ascii=False).encode()+b'\n'


def test_identity_includes_project_adapter_and_agent():
    raw={'kind':'external','session_id':'parent','agent_id':'main'}
    a=RuntimeIdentity.parse('P','codex',raw)
    assert a.key==RuntimeIdentity.parse('P','codex',raw).key
    assert a.key!=RuntimeIdentity.parse('P','codex',{**raw,'agent_id':'child'}).key
    assert a.key!=RuntimeIdentity.parse('Q','codex',raw).key


def test_adapter_single_call_binds_task_and_all_user_events(project,tmp_path):
    project['cfg']['batch']['message_source']={'id':'codex-local','mode':'runtime_event'}
    write_json(project['config_path'],project['cfg'])
    path=tmp_path/'rollout.jsonl';path.write_bytes(logline('user_message')+logline('agent_message')+logline('user_message','дальше'))
    a=RuntimeAdapter(settings(project,tmp_path))
    p=packet(boot(project),transcript={'path':str(path),'initial_offset':0})
    out=a.invoke(p)
    assert out['task']=='T1' and out['interaction']['observed_messages_count']==2
    p['work']=request('show',{'queries':[{'id':'m','kind':'messages'}]})
    out2=RuntimeAdapter(settings(project,tmp_path)).invoke(p)
    assert out2['runtime_adapter']['session_id']==out['runtime_adapter']['session_id']
    assert out2['interaction']['observed_messages_count']==2
    with path.open('ab') as f:f.write(logline('user_message','read-only question'))
    assert a.invoke(p)['interaction']['observed_messages_count']==3


def test_partial_final_record_is_not_consumed(project,tmp_path):
    path=tmp_path/'log';first=logline('user_message');second=logline('user_message','second')
    path.write_bytes(first+second[:-2])
    source=CodexTranscript(settings(project,tmp_path))
    batch=source.read(str(path),0,None,'conversation')
    assert len(batch['messages'])==1 and batch['cursor']['offset']==len(first)
    with path.open('ab') as f:f.write(second[-2:])
    next_=source.read(str(path),0,batch['cursor'],'conversation')
    assert len(next_['messages'])==1 and next_['messages'][0]['message_id']!=batch['messages'][0]['message_id']


def test_truncation_or_changed_anchor_is_not_silent_reset(project,tmp_path):
    path=tmp_path/'log';path.write_bytes(logline('user_message'))
    source=CodexTranscript(settings(project,tmp_path));b=source.read(str(path),0,None,'c')
    path.write_bytes(logline('user_message','changed'))
    with pytest.raises(HarnessError):source.read(str(path),0,b['cursor'],'c')


def test_unexpected_replica_response_item_is_not_double_counted(project,tmp_path):
    path=tmp_path/'log';path.write_bytes(logline('user_message')+json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[]}}).encode()+b'\n')
    b=CodexTranscript(settings(project,tmp_path)).read(str(path),0,None,'c')
    assert len(b['messages'])==1


def test_no_capability_means_not_available():
    with pytest.raises(HarnessError):validate_inventory([],['rename'])
    assert validate_inventory([],[])==[]


def test_missing_required_capability_blocks_before_task_creation(project,tmp_path):
    cfg=settings(project,tmp_path);cfg['required_capabilities']=['rename']
    with pytest.raises(HarnessError):RuntimeAdapter(cfg).invoke(packet(boot(project)))
    from harness.runtime import Harness
    assert Harness(project['config_path'],'check').task_queries.record('T1') is None


def test_generated_session_reuses_launch_request_but_separates_launches(project,tmp_path):
    a=RuntimeAdapter(settings(project,tmp_path));work=request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None})
    p=packet(work);p['identity']={'kind':'generated','request_id':'launchA','agent_id':'main'}
    x=a.invoke(p)['runtime_adapter']['session_id']
    assert a.invoke(p)['runtime_adapter']['session_id']==x
    p['identity']['request_id']='launchB'
    assert a.invoke(p)['runtime_adapter']['session_id']!=x


def test_transcript_outside_explicit_roots_rejected(project,tmp_path):
    cfg=settings(project,tmp_path);cfg['transcript_roots']=[str(tmp_path/'allowed')]
    path=tmp_path/'outside';path.write_bytes(logline('user_message'))
    with pytest.raises(HarnessError):CodexTranscript(cfg).read(str(path),0,None,'c')


def test_unknown_transcript_source_cannot_be_labelled_agent_reported(project,tmp_path):
    path=tmp_path/'log';path.write_bytes(logline('user_message'))
    with pytest.raises(HarnessError):RuntimeAdapter(settings(project,tmp_path)).invoke(packet(boot(project),transcript={'path':str(path),'initial_offset':0}))


def test_scan_budget_does_not_drop_unread_messages(project,tmp_path):
    cfg=settings(project,tmp_path);cfg['max_events']=1
    path=tmp_path/'log';path.write_bytes(logline('user_message')*2)
    b=CodexTranscript(cfg).read(str(path),0,None,'c')
    assert b['complete_snapshot'] is False
    assert len(b['messages'])==1
    c=CodexTranscript(cfg).read(str(path),0,b['cursor'],'c')
    assert len(c['messages'])==1 and c['messages'][0]['message_id']!=b['messages'][0]['message_id']
