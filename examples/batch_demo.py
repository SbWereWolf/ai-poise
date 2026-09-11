#!/usr/bin/env python3
"""One instruction per stage, one packet per operation. All review text is fixture data."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from demo import create,save,SOURCE
from work_client import WorkClient


def user(identifier,reason):
    return {'conversation_id':'batch-demo','message_id':identifier,
            'occurred_at':None,'reason':reason,'subject':None}


def run(directory):
    home=create(directory)
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'POISE_CONFIG':str(home/'project.json'),'POISE_SESSION':'batch-demo'}
    client=WorkClient(env,30)
    task=json.loads((home/'task.json').read_text());task['sprint_id']='SPRINT-DEMO'
    draft=client.invoke('sprint',{'action':'draft','sprint_id':'SPRINT-DEMO','request_id':'demo-draft',
       'expected_revision':None,'template':{'id':'basic','version':'1'},'changes':[
       {'kind':'purpose','goal':'Учебный спринт','requirements':['Исправить double'],'definition_of_done':['Пройден RED/GREEN']},
       {'kind':'sections','values':{'plan':'Выполнить задачу разработки по её четырём этапам.'}},
       {'kind':'upsert_tasks','tasks':[task]},{'kind':'dependencies','items':[]}]})
    client.invoke('sprint',{'action':'publish','sprint_id':None,'request_id':'demo-publish','expected_revision':draft['revision']})
    reports=[]
    for i,sid in enumerate(['tests','test_review','implementation','code_review']):
        event=user(f'turn-{i}', 'initial' if i==0 else 'continue')
        b=client.invoke('bootstrap',{'task':task if i==0 else None,'decision':None if i==0 else 'continue',
                                     'feedback':None,'rework_stage':None},messages=[event])
        assert b['stage']==sid and 'result_path' not in b
        wt=Path(b['worktree'])
        if sid=='tests':
            (wt/'tests').mkdir()
            (wt/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
        if sid=='implementation':(wt/'src/double.py').write_text('def double(n):\n    return n*2\n')
        payload=deepcopy(b['result_template'])
        payload['sections']['report']=f'Fixture result of {sid}; not an independent reviewer.'
        payload['commit_message']=f'demo: {sid}'
        artifacts=[]
        if sid=='code_review':
            artifacts=[{'scope':'task','path':'result.md','source':{'kind':'template','id':'note','version':'1','values':{'title':'Result','body':'RED → GREEN; double(2) = 4.'}}},
                       {'scope':'sprint','path':'shared.md','source':{'kind':'text','text':'Shared result of DEMO-1'}},
                       {'scope':'runtime','path':'scratch.txt','source':{'kind':'text','text':'Temporary'}}]
        report=client.invoke('verify',{'result':payload,'artifacts':artifacts},messages=[event])
        assert report['status']=='verified' and report['interaction']['user_messages_count']==i+1
        assert not Path(b['runtime_root']).exists()
        repeat=client.invoke('verify',{'result':payload,'artifacts':artifacts},messages=[event])
        assert repeat['replayed'] and repeat['interaction']['user_messages_count']==i+1
        reports.append(report)
    final=client.invoke('accept',{},messages=[user('accept','authorization')])
    assert final['status']=='completed' and final['interaction']['user_messages_count']==5
    batch=client.invoke('show',{'queries':[{'id':'section','kind':'section','name':'report','stage':None,'submission':None,'range':None},
        {'id':'messages','kind':'messages'},{'id':'state','kind':'task'}]})
    m=batch['results'][1]['value']
    assert m['delivered_stages_count']==4 and m['delivered_iterations_count']==4
    out={'status':'PASS','task_status':'completed','messages':m,'reports':len(reports),
         'artifact_paths':[r['path'] for r in reports[-1]['artifacts']],
         'no_runtime_result_file':True,'calls':client.calls}
    save(directory/'batch-demo-report.json',out)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True,type=Path)
    print(json.dumps(run(p.parse_args().directory.resolve()),ensure_ascii=False,indent=2))
