#!/usr/bin/env python3
"""Real CLI actions. Repository setup, user instructions and reviews are fixtures."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import os
import sys
from demo import create,save,git,SOURCE
from work_client import WorkClient


def stage(name,handler,edges,readonly,paths):
    return {'id':name,'handler':handler,'transitions':edges,
            'rework_targets':['apply'] if handler=='publish' else [name],
            'instruction':f'Выполнить {name}, проверить и доложить.',
            'read_only':readonly,'allowed_paths':paths,'normalization':'strip',
            'sections':{} if handler=='publish' else {'report':'Заполнить.'},
            'required_sections':[] if handler=='publish' else ['report'],'artifact_requirements':[]}


def process(kind):
    integration=kind=='integration'
    end='publish' if integration else None
    stages=[stage('apply','apply_plan',{'complete':'inspect'},False,['src/**','docs/**']),
            stage('inspect','inspect',{'clear':end,'changes_requested':'correct'},True,[]),
            stage('correct','revise' if integration else 'apply_plan',{'complete':'followup'},False,['src/**','docs/**']),
            stage('followup','inspect',{'clear':end,'changes_requested':'correct'},True,[])]
    if integration:stages.append(stage('publish','publish',{'complete':None},True,[]))
    return {'goal_type':kind,'benefit':{'git_categories':[], 'sections':[]},'route':{'entry':'apply','max_transitions':20,'max_stage_visits':5},
            'content_contract':{'sections':[],'routes':[],'requirements':[]},'stages':stages}


def method(mid,code):
    return {'id':mid,'argv':[sys.executable,'-B','-c',code],'cwd':'.','environment':{},
            'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},
            'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}


def external_method(mid,code):
    return {'id':mid,'argv':[sys.executable,'-B','-c',code],'cwd':'.','environment':{},
            'source_under_test':{'kind':'external','reason':'The service-state command reads no repository source.'},
            'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}


def submission(ctx,work):
    value=deepcopy(ctx['result_template'])
    value['sections']={key:'Демонстрационный результат; решения пользователя и осмотра заданы fixture.' for key in value['sections']}
    value['stage_work']=work;value['commit_message']='demo: verified integrated result'
    return value


def run(directory,scenario):
    home=create(directory);app=directory/'application';cfg=json.loads((home/'project.json').read_text())
    kind='integration' if scenario=='integration_conflict' else 'environment_remediation'
    definition=process(kind);save(home/'config/processes/actions.json',definition)
    cfg['processes']={kind:'config/processes/actions.json'};cfg['automatic_checks']=[];save(home/'project.json',cfg)
    base=git(app,'rev-parse','HEAD');sources=[]
    if kind=='integration':
        for name,text in [('left','def double(n):\n    return n * 2\n'),('right','def double(n):\n    return n + n\n')]:
            git(app,'checkout','-b',name,base);(app/'src/double.py').write_text(text)
            git(app,'add','--all');git(app,'commit','-m',f'Existing source {name}')
            sources.append({'commit':git(app,'rev-parse','HEAD'),'checkpoint_message':f'WIP: integrate source {name}'})
        git(app,'checkout','main')
    check=method('CHECK','from src.double import double; assert double(4)==8')
    names=[s['id'] for s in definition['stages']]
    task={'id':'ACTIONS','sprint_id':None,'goal_type':kind,'goal':'Проверенное объединение результатов' if kind=='integration' else 'Настроить учебную среду',
          'requirements':['Сохранить требуемое поведение'],'definition_of_done':['Состояние подтверждено и осмотрено'],
          'methods':[check] if kind=='integration' else [],
          'method_inputs':[{'method_id':'CHECK','repository_inputs':[],'future_outputs':[],
              'reference_profile':{'runner':'python','parser':'inline-no-path-arguments','version':1}}] if kind=='integration' else [],
          'checks':{name:(['CHECK'] if kind=='integration' and name!='publish' else []) for name in names},
          'artifact_requirements':[],'content_contract':{'sections':[],'routes':[],'requirements':[]},
          'evidence_plan':{name:{'subject_methods':{},'arguments':[],'review_arguments':[]} for name in names}}
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src'),'POISE_CONFIG':str(home/'project.json'),'POISE_SESSION':'actions-demo'}
    client=WorkClient(env,30);ctx=client.bootstrap(task);reports=[]
    clean={'coverage':'Осмотрены изменения и доказательства. Решение задано fixture.','findings':[],'resolution_decisions':[]}
    if kind=='integration':
        plan={'kind':'git_merge','base_commit':base,'sources':sources}
        value=submission(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
        result=client.verify(value,[])
        assert result['status']=='awaiting_action_continuation'
        assert result['action']['conflicts']==['src/double.py']
        assert git(app,'ls-remote','backup','refs/heads/main').split()[0]==base
        # Simulates the agent's native source editor, not a Poise-data edit.
        Path(ctx['worktree'],'src/double.py').write_text('def double(n):\n    return n * 2\n')
        value=result['context']['result_template']
        value['stage_work']['resolutions']=[{'path':'src/double.py','reason':'Оба источника требуют удвоения; сохраняется общее поведение.'}]
        result=client.verify(value,[]);assert result['status']=='verified';reports.append(result)
        integrated=result['commit'];assert client.verify(None,[])['replayed']
        ctx=client.bootstrap(None,decision='continue')
        review=client.verify(submission(ctx,clean),[]);assert review['status']=='verified';reports.append(review)
        assert git(app,'ls-remote','backup','refs/heads/main').split()[0]==base
        ctx=client.bootstrap(None,decision='continue')
        published=client.verify(submission(ctx,{'target_ref':'refs/heads/main','expected_commit':base,
                                               'authorization':'Пользователь принял осмотр и поручил публикацию.'}),[])
        assert published['status']=='verified';reports.append(published)
        assert git(app,'ls-remote','backup','refs/heads/main').split()[0]==integrated
        assert git(app,'rev-parse','main')==base  # local checked-out main was never changed
    else:
        # A controlled file models external service configuration; no real OS setup.
        service=directory/'service-state.txt'
        def plan(value):
            return {'kind':'commands','steps':[{'id':'service','probe_false_exit_codes':[1],
              'apply':external_method('APPLY',f'from pathlib import Path; Path({str(service)!r}).write_text({value!r})'),
              'probe':external_method('PROBE',f'from pathlib import Path; assert Path({str(service)!r}).read_text()=={value!r}')}]}
        applied=client.verify(submission(ctx,{'plan':plan('v1'),'phase':'prepare','resolutions':[],'finding_resolutions':[]}),[])
        assert applied['status']=='verified';reports.append(applied)
        ctx=client.bootstrap(None,decision='continue')
        reviewed=client.verify(submission(ctx,{**clean,'findings':[{'id':'E1','subject':'service-state',
          'description':'Нужен уточнённый v2.','evidence':'Учебный пример feedback; это не автоматическое заключение.'}]}),[])
        assert reviewed['stage_outcome']=='changes_requested';reports.append(reviewed)
        ctx=client.bootstrap(None,decision='continue')
        corrected=client.verify(submission(ctx,{'plan':plan('v2'),'phase':'prepare','resolutions':[],
          'finding_resolutions':[{'id':'ER1','finding_id':'E1','description':'Применён v2.','evidence':'Фактический probe выполняет Poise.'}]}),[])
        assert corrected['status']=='verified' and service.read_text()=='v2';reports.append(corrected)
        ctx=client.bootstrap(None,decision='continue')
        follow=client.verify(submission(ctx,{**clean,'resolution_decisions':[{'resolution_id':'ER1','decision':'accepted',
           'reason':'Результат и probe осмотрены; fixture.'}]}),[])
        assert follow['stage_outcome']=='clear';reports.append(follow)
    final=client.accept();assert final['status']=='completed'
    output={'status':'PASS','scenario':scenario,'task_status':final['status'],
            'reports':[{k:r[k] for k in ('stage','handler','status','commit')} for r in reports],
            'calls':client.calls,'review_is_fixture':True,'network_used':False}
    save(directory/'actions-demo-report.json',output);return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--scenario',choices=['integration_conflict','environment_feedback'],required=True)
    args=parser.parse_args();print(json.dumps(run(args.directory.resolve(),args.scenario),ensure_ascii=False,indent=2))
