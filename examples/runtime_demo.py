#!/usr/bin/env python3
"""Local runtime binding, observed JSONL messages, WIP handoff and durable views.

The rollout and review decisions are test fixtures, not a live Codex installation.
No network, user repository, Gmail or external system changes are made.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from demo import create,save,SOURCE


class RuntimeClient:
    def __init__(self,settings,transcript,agent):
        self.settings,self.transcript,self.agent=settings,transcript,agent
        self.calls=[]
        self.environment={k:v for k,v in os.environ.items() if k not in ('POISE_SESSION','POISE_CONFIG')}
        self.environment['PYTHONPATH']=str(SOURCE/'src')

    def invoke(self,operation,inputs):
        packet={'identity':{'kind':'external','session_id':'runtime-demo','agent_id':self.agent},
                'capabilities':[], 'transcript':{'path':str(self.transcript),'initial_offset':0},
                'work':{'operation':operation,'input':inputs,'messages':[]}}
        proc=subprocess.run([sys.executable,'-B','-m','poise','runtime','--settings',str(self.settings)],
                input=json.dumps(packet),text=True,capture_output=True,env=self.environment,timeout=30)
        if proc.returncode:raise RuntimeError(proc.stdout+proc.stderr)
        brief=json.loads(proc.stdout)
        result=json.loads(Path(brief['response_path']).read_text()) if 'response_path' in brief else brief
        self.calls.append({'operation':operation,'status':result['status']})
        return result


def run(directory):
    home=create(directory)
    cfg=json.loads((home/'project.json').read_text())
    cfg['batch']['message_source']={'id':'codex-file','mode':'runtime_event'}
    save(home/'project.json',cfg)
    transcript=directory/'rollout.jsonl';transcript.write_text('')
    settings=directory/'runtime-adapter.json'
    save(settings,{'schema':'runtime-adapter-1','project_config':str(home/'project.json'),
        'adapter_id':'codex-file','transcript_roots':[str(directory)],'max_scan_bytes':1048576,
        'max_line_bytes':65536,'max_events':100,'required_capabilities':[]})
    def user(text):
        with transcript.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'timestamp':None,'type':'event_msg',
                 'payload':{'type':'user_message','message':text}},ensure_ascii=False)+'\n')
    def begin(client,task,decision):
        return client.invoke('bootstrap',{'task':task,'decision':decision,'feedback':None,'rework_stage':None})
    a=RuntimeClient(settings,transcript,'A');b=RuntimeClient(settings,transcript,'B')
    task=json.loads((home/'task.json').read_text())
    user('Start task; write regression test')
    ctx=begin(a,task,None);worktree=Path(ctx['worktree'])
    (worktree/'tests').mkdir()
    (worktree/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2), 4)\n')
    files=a.invoke('artifacts',{'items':[{'scope':'runtime','path':'note.txt',
        'source':{'kind':'text','text':'Prepared regression; not yet verified.'}}]})
    payload=deepcopy(ctx['result_template']);payload['sections']['report']='Unfinished test stage, preserve before verification.'
    payload['artifact_paths']=files['artifact_paths']
    user('Hand off to another agent')
    handoff=a.invoke('handoff',{'request_id':'handoff-A','reason':'Continue with B',
                    'result':payload,'commit_message':'WIP: regression','artifact_paths':[]})
    assert not handoff['verified'] and Path(handoff['bundle_path']).is_file()
    user('Resume saved task')
    ctx=begin(b,{'id':task['id']},None)
    assert ctx['worktree']==str(worktree) and ctx['stage']=='tests'
    reports=[];full_read=None
    for index,sid in enumerate(('tests','test_review','implementation','code_review')):
        if index:
            user('Continue one stage')
            ctx=begin(b,None,'continue')
        assert ctx['stage']==sid
        if sid=='implementation':(worktree/'src/double.py').write_text('def double(n):\n    return n * 2\n')
        result=deepcopy(ctx['result_template']);result['sections']['report']=f'Fixture report: {sid}; no independent model review.'
        result['commit_message']=f'demo: {sid}'
        artifacts=[] if sid!='code_review' else [{'scope':'task','path':'result.md',
            'source':{'kind':'text','text':'WIP resumed; RED then GREEN. Full evidence retained.'}}]
        report=b.invoke('verify',{'result':result,'artifacts':artifacts})
        assert report['status']=='verified' and not Path(ctx['runtime_root']).exists()
        repeat=b.invoke('verify',{'result':result,'artifacts':artifacts})
        assert repeat['replayed']
        if sid=='tests':
            receipt=report['checks'][0]
            full_read=b.invoke('show',{'queries':[{'id':'full','kind':'tool_result','receipt_id':receipt['id'],
                                                   'representation':'full','range':None}]})['results'][0]['value']
            assert 'test_double' in full_read['text']
        reports.append(report)
    user('Accept final result')
    final=b.invoke('accept',{})
    assert final['status']=='completed' and final['interaction']['user_messages_count']==7
    out={'status':'PASS','handoff':handoff,'final_status':final['status'],'messages':final['interaction'],
         'same_worktree':True,'same_iteration_resumed':True,'reports':len(reports),
         'red_exit':reports[0]['checks'][0]['actual_exit_code'],'green_exit':reports[2]['checks'][0]['actual_exit_code'],
         'full_read_after_cleanup':full_read['total_bytes'],'calls_A':a.calls,'calls_B':b.calls,
         'source':'synthetic local JSONL in supported observed shape; not live hook install'}
    save(directory/'runtime-demo-report.json',out)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    result=run(parser.parse_args().directory.resolve())
    print(json.dumps({k:v for k,v in result.items() if k not in ('calls_A','calls_B','handoff')},ensure_ascii=False,indent=2))
