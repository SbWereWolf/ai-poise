import json
import os
from pathlib import Path
import subprocess
import sys
from copy import deepcopy
from .helpers import setup,task,changes
from batch.helpers import request


def call(project,packet,session='sprint-cli'):
    r=subprocess.run([sys.executable,'-m','poise','work'],input=json.dumps(packet),capture_output=True,text=True,
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
             'POISE_CONFIG':str(project['config_path']),'POISE_SESSION':session},timeout=20)
    view=json.loads(r.stdout)
    return r,json.loads(Path(view['response_path']).read_text()) if 'response_path' in view else view


def test_cli_draft_publish_bootstrap_and_persistent_receipt(project):
    setup(project)
    raw=request('sprint',{'action':'draft','sprint_id':'S','request_id':'d','expected_revision':None,
        'template':{'id':'basic','version':'1'},'changes':changes([task(project)])})
    r,d=call(project,raw);assert r.returncode==0 and d['revision']==1
    r,p=call(project,request('sprint',{'action':'publish','sprint_id':None,'request_id':'p','expected_revision':d['revision']}))
    assert r.returncode==0 and p['eligible']==['A']
    r,b=call(project,request('bootstrap',{'task':{'id':'S'},'decision':None,'feedback':None,'rework_stage':None}))
    assert r.returncode==0 and b['eligible']==['A'] and b['active_task'] is None
    receipt=json.loads(r.stdout)['response_path'];assert '/sprints/S/' in receipt and Path(receipt).is_file()
    assert len(r.stdout)<=project['cfg']['limits']['output_chars']


def test_cli_invalid_publication_returns_rejected_no_worktree(project):
    setup(project);t=task(project);t['checks']['work']=['MISSING']
    r,d=call(project,request('sprint',{'action':'draft','sprint_id':'S','request_id':'d','expected_revision':None,
        'template':{'id':'basic','version':'1'},'changes':changes([t])}))
    assert r.returncode==0 and d['errors']
    r,p=call(project,request('sprint',{'action':'publish','sprint_id':None,'request_id':'p','expected_revision':d['revision']}))
    assert r.returncode==2 and p['status']=='rejected'
    assert not (project['root']/'state/worktrees').exists()


def test_sprint_demo_happy_and_feedback_paths(tmp_path):
    root=Path(__file__).resolve().parents[2];where=tmp_path/'demo'
    r=subprocess.run([sys.executable,str(root/'examples/sprint_demo.py'),'--directory',str(where)],
         capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(root/'src')},timeout=90)
    assert r.returncode==0,r.stdout+r.stderr
    out=json.loads((where/'sprint-demo-report.json').read_text())
    assert out['status']=='PASS' and out['task_count']==3 and out['stage_reports']==8
    assert out['planning_feedback'] and out['implementation_feedback'] and out['result_dependency_received']


def test_cli_rejects_retired_task_replacement_packet(project):
    setup(project)
    raw=request('sprint',{'action':'draft','sprint_id':'S','request_id':'d','expected_revision':None,
        'template':{'id':'basic','version':'1'},'changes':changes([task(project,'BAD')])})
    _,drafted=call(project,raw)
    _,published=call(project,request('sprint',{'action':'publish','sprint_id':None,
        'request_id':'p','expected_revision':drafted['revision']}))
    packet=request('sprint',{'action':'replace_task','sprint_id':'S','request_id':'replace-BAD',
        'expected_revision':published['revision'],'source_task':'BAD','replacement':task(project,'BAD-2'),
        'reason':'Исправить ошибочный метод','authorization':'Пользователь разрешил замену'})

    result,body=call(project,packet)

    assert result.returncode==2
    assert body=={'status':'rejected','reason':'Unknown sprint action'}
