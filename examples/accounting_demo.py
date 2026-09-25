#!/usr/bin/env python3
"""Actual CLI/Git/SQLite; user decisions and token events are explicit synthetic fixtures."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from datetime import datetime,timedelta,timezone
from demo import create,SOURCE
from work_client import WorkClient


def main():
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True,type=Path);a=p.parse_args()
    home=create(a.directory)
    cfg=json.loads((home/'project.json').read_text())
    task=json.loads((home/'task.json').read_text())
    env={**{k:v for k,v in os.environ.items() if k not in ('CODEX_SESSION_ID','CODEX_THREAD_ID','POISE_CALLER_BINDING','POISE_SESSION')},'PYTHONPATH':str(SOURCE/'src'),'POISE_CONFIG':str(home/'project.json'),'POISE_CALLER_BINDING':str(home/'accounting-caller.json')}
    client=WorkClient(env,30);seq=0
    usage_epoch=datetime(2026,9,12,tzinfo=timezone.utc)
    def msg(mid):return {'conversation_id':'demo','message_id':mid,'occurred_at':datetime.now(timezone.utc).isoformat(),'reason':None,'subject':None}
    def usage():
        nonlocal seq
        seq+=1
        return {'usage':[{'source':'test','stream':'demo-stream','event_id':str(seq),'sequence':seq,'mode':'delta',
            'occurred_at':(usage_epoch+timedelta(seconds=seq)).isoformat(),'counters':{'input_tokens':1000,'output_tokens':200,'total_tokens':1200,'cached_input_tokens':300,'reasoning_tokens':50}}],
            'intervals':[],'cause':'initial','finding_targets':[]}
    reports=[]
    for i,sid in enumerate(('tests','test_review','implementation','code_review')):
        c=client.invoke('bootstrap',{'task':task if i==0 else None,'decision':None if i==0 else 'continue','feedback':None,'rework_stage':None},messages=[msg(f'work-{i}')])
        wt=Path(c['worktree'])
        if sid=='tests':
            (wt/'tests').mkdir();(wt/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
        if sid=='implementation':(wt/'src/double.py').write_text('def double(n):\n    return n * 2\n')
        v=deepcopy(c['result_template']);v['sections']['report']='Содержательный результат проверен.';v['commit_message']='feat: accounting demo result'
        artifacts=[]
        if sid=='code_review':artifacts=[{'scope':'task','path':'result.md','source':{'kind':'text','text':'Проверенный итог'}}]
        reports.append(client.invoke('verify',{'result':v,'artifacts':artifacts},telemetry=usage()))
    client.invoke('accept',{},messages=[msg('accepted')])
    cancelled=deepcopy(task);cancelled['id']='CANCELLED';cancelled['artifact_requirements']=[]
    client.invoke('bootstrap',{'task':cancelled,'decision':None,'feedback':None,'rework_stage':None},messages=[msg('cancel-start')])
    client.invoke('cancel',{'reason':'User decided not to continue'},messages=[msg('cancel')],telemetry=usage())
    q={'id':'economics','kind':'accounting','scope':{'kind':'all','id':None},'group_by':['goal_type'],'from':None,'to':None}
    result=client.invoke('show',{'queries':[q]})['results'][0]['value']
    assert result['totals']['model_tokens']==6000
    assert result['totals']['cancelled_tokens']==1200
    assert result['totals']['benefit']['changed_lines']==2
    assert result['totals']['user_messages_count']==7
    assert result['totals']['benefit']['changed_tokens'] is None
    report={'status':'PASS','token_events_are_synthetic':True,'receipt_steps':len(reports),'economics':result}
    out=a.directory/'accounting-report.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'PASS','report':str(out),'tokens_fixture':6000,'cancelled_tokens_fixture':1200,'useful_lines':2,'user_messages':7},ensure_ascii=False))

if __name__=='__main__':main()
