#!/usr/bin/env python3
"""Small real CLI Sprint walkthrough. Source setup and reviewer decisions are fixtures."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from demo import create, save, SOURCE
from work_client import WorkClient


def stage(name,kind,next_steps,readonly,paths):
    return {'id':name,'handler':kind,'transitions':next_steps,'rework_targets':[name],
            'instruction':'Выполнить текущий этап и доложить.', 'read_only':readonly,'allowed_paths':paths,
            'normalization':'strip','sections':{'report':'Заполнить.'},'required_sections':['report'],'artifact_requirements':[]}


def process(kind):
    paths=['src/**'] if kind=='development' else ['docs/**']
    return {'goal_type':kind,'benefit':{'git_categories':[], 'sections':[]},'route':{'entry':'write','max_transitions':12,'max_stage_visits':4},
       'content_contract':{'sections':[],'routes':[],'requirements':[]},'stages':[
       stage('write','produce',{'complete':'inspect'},False,paths),
       stage('inspect','inspect',{'clear':None,'changes_requested':'amend'},True,[]),
       stage('amend','revise',{'complete':'confirm'},False,paths),
       stage('confirm','inspect',{'clear':None,'changes_requested':'amend'},True,[])]}


def task(tid,kind,command):
    return {'id':tid,'sprint_id':'SPRINT','goal_type':kind,'goal':f'Создать результат {tid}',
       'requirements':[f'R-{tid}'],'definition_of_done':[f'Результат {tid} проверен'],
       'methods':[{'id':'CHECK','argv':[sys.executable,'-B','-c',command],'cwd':'.','environment':{},
                   'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}],
       'checks':{name:['CHECK'] for name in ('write','inspect','amend','confirm')},
       'artifact_requirements':[],'content_contract':{'sections':[],'routes':[],'requirements':[]},
       'evidence_plan':{name:{'subject_methods':{},'arguments':[],'review_arguments':[]} for name in ('write','inspect','amend','confirm')}}


def run(directory):
    home=create(directory)
    baseline_source=(directory/'application/src/double.py').read_text()
    cfg=json.loads((home/'project.json').read_text());cfg['automatic_checks']=[]
    for kind in ('development','documentation'):
        save(home/f'config/processes/{kind}.json',process(kind))
        cfg['processes'][kind]=f'config/processes/{kind}.json'
    save(home/'project.json',cfg)
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'POISE_CONFIG':str(home/'project.json'),'POISE_SESSION':'sprint-demo'}
    client=WorkClient(env,30)
    a=task('A','development','from src.double import double; assert double(2)==4')
    b=task('B','documentation','from src.double import double; assert double(0)==0')
    c=task('C','documentation','print("independent")')
    incomplete=deepcopy(b);del incomplete['methods']
    r=client.invoke('sprint',{'action':'draft','sprint_id':'SPRINT','request_id':'draft-1','expected_revision':None,
       'template':{'id':'basic','version':'1'},'changes':[
       {'kind':'purpose','goal':'Код и инструкция','requirements':['Исправление и инструкция'],'definition_of_done':['Три задачи приняты']},
       {'kind':'sections','values':{'plan':'A и C независимы; B использует код A.'}},
       {'kind':'upsert_tasks','tasks':[a,incomplete,c]},
       {'kind':'dependencies','items':[{'predecessor':'A','successor':'B','kind':'result'}]}]})
    assert r['errors'] and not (home/'state/worktrees').exists()
    r=client.invoke('sprint',{'action':'draft','sprint_id':None,'request_id':'draft-2','expected_revision':r['revision'],
       'template':None,'changes':[{'kind':'upsert_tasks','tasks':[b]}]})
    assert not r['errors']
    r=client.invoke('sprint',{'action':'publish','sprint_id':None,'request_id':'publish','expected_revision':r['revision']})
    assert set(r['eligible'])=={'A','C'} and not (home/'state/worktrees').exists()
    reports=[]
    for tid,feedback in [('C',False),('A',True),('B',False)]:
        stages=['write','inspect','amend','confirm'] if feedback else ['write','inspect']
        ctx=None
        for index,name in enumerate(stages):
            ctx=client.bootstrap({'id':tid} if index==0 else None,decision=None if index==0 else 'continue')
            assert ctx['stage']==name
            wt=Path(ctx['worktree'])
            if name=='write':
                if tid=='A':(wt/'src/double.py').write_text('def double(n):\n    return 4\n')
                else:
                    (wt/'docs').mkdir(exist_ok=True);(wt/f'docs/{tid}.md').write_text(f'Документ {tid}.\n')
            if name=='amend':(wt/'src/double.py').write_text('def double(n):\n    return n*2\n')
            value=deepcopy(ctx['result_template']);value['sections']['report']=f'Fixture report {tid}/{name}'
            value['commit_message']=f'demo: {tid} {name}'
            if name in ('inspect','confirm'):
                findings=[{'id':'F1','subject':'src/double.py','description':'double(0) возвращает 4 вместо 0',
                           'evidence':'Осмотр константной реализации; решение задано fixture.'}] if feedback and name=='inspect' else []
                decisions=[{'resolution_id':'R1','decision':'accepted','reason':'Исправление осмотрено; fixture decision.'}] if name=='confirm' else []
                value['stage_work']={'coverage':'Проверен описанный scope.','findings':findings,'resolution_decisions':decisions}
            if name=='amend':
                value['stage_work']={'resolutions':[{'id':'R1','finding_id':'F1','description':'Общий расчёт n*2',
                    'evidence':'Проверяется zero case зарегистрированной командой.'}]}
                value['method_additions']=[{'method':{'id':'ZERO','argv':[sys.executable,'-B','-c','from src.double import double; assert double(0)==0'],
                    'cwd':'.','environment':{},'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]},
                    'stages':['amend','confirm']}]
            out=client.verify(value,[]);assert out['status']=='verified'
            reports.append({'task':tid,'stage':name,'commit':out['commit']})
        accepted=client.accept();assert accepted['status']=='completed'
    final=client.bootstrap({'id':'SPRINT'})
    assert final['status']=='completed' and not final['eligible']
    assert baseline_source == (directory/'application/src/double.py').read_text()
    out={'status':'PASS','sprint':'completed','task_count':3,'stage_reports':len(reports),
         'planning_feedback':True,'implementation_feedback':True,'source_main_unchanged':True,
         'result_dependency_received':True,'reports':reports,'calls':client.calls}
    save(directory/'sprint-demo-report.json',out)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True,type=Path)
    print(json.dumps(run(p.parse_args().directory.resolve()),ensure_ascii=False,indent=2))
