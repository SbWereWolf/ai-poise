from copy import deepcopy
from pathlib import Path
import sys
from conftest import git,write_json
from harness.runtime import Harness
from harness.application.work import WorkTools


def call(h,op,inp,messages=None):
    return WorkTools(h).invoke({'operation':op,'input':inp,'messages':[] if messages is None else messages})


def start(h,task):
    return call(h,'bootstrap',{'task':task,'decision':None,'feedback':None,'rework_stage':None})


def advance(h):
    return call(h,'bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None})


def result(ctx,work=None):
    p=deepcopy(ctx['result_template'])
    p['sections']={key:'Результат этапа подготовлен.' for key in p['sections']}
    p['commit_message']='integration: verified sources'
    if work is not None:p['stage_work']=work
    return p


def verify(h,p):
    return call(h,'verify',{'result':p,'artifacts':[]})


def method(mid,code):
    return {'id':mid,'argv':[sys.executable,'-B','-c',code],'cwd':'.','environment':{},
            'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}


def setup(project, kind='git_merge', conflict=True):
    app=project['app'];base=git(app,'rev-parse','HEAD')
    sources=[]
    if kind=='git_merge':
        for branch,code in [('left','def double(n):\n    return n * 2\n'),
                            ('right','def double(n):\n    return n + n\n' if conflict else 'DOCUMENT ONLY')]:
            git(app,'checkout','-b',branch,base)
            file=app/'src/double.py' if code!='DOCUMENT ONLY' else app/'README.md'
            file.write_text(code)
            git(app,'add','--all');git(app,'commit','-m',f'Existing {branch} result')
            sources.append({'commit':git(app,'rev-parse','HEAD'),'checkpoint_message':f'WIP integration {branch}'})
        git(app,'checkout','main')
    stages=[]
    for name,handler,transitions,ro in [
      ('apply','apply_plan',{'complete':'review'},False),
      ('review','inspect',{'clear':'publish','changes_requested':'fix'},True),
      ('fix','revise',{'complete':'recheck'},False),
      ('recheck','inspect',{'clear':'publish','changes_requested':'fix'},True),
      ('publish','publish',{'complete':None},True)]:
        if kind=='commands' and name=='publish':continue
        if kind=='commands' and name=='fix':handler='apply_plan'
        t=deepcopy(transitions)
        if kind=='commands':t={k:(None if v=='publish' else v) for k,v in t.items()}
        stages.append({'id':name,'handler':handler,'transitions':t,'rework_targets':[name] if name!='publish' else ['apply'],
                       'read_only':ro,'allowed_paths':[] if ro else ['src/**','README.md'],
                       'instruction':f'Выполнить {name}', 'normalization':'strip',
                       'sections':{} if handler=='publish' else {'report':'Заполнить.'},
                       'required_sections':[] if handler=='publish' else ['report'],'artifact_requirements':[]})
    process={'goal_type':'integration' if kind=='git_merge' else 'environment_remediation','benefit':{'git_categories':[], 'sections':[]},
             'stages':stages,'route':{'entry':'apply','max_transitions':30,'max_stage_visits':5},
             'content_contract':{'sections':[],'routes':[],'requirements':[]}}
    cfg=deepcopy(project['cfg']); cfg['processes']={process['goal_type']:'config/processes/action.json'};cfg['automatic_checks']=[]
    write_json(project['root']/'config/processes/action.json',process)
    write_json(project['config_path'],cfg)
    task=deepcopy(project['task']);task['goal_type']=process['goal_type'];task['id']='INTEGRATE';task['methods']=[]
    task['checks']={s['id']:[] for s in stages};task['evidence_plan']={s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in stages}
    if kind=='git_merge':
        task['methods']=[method('COMBINED','from src.double import double; assert double(4)==8; print("combined OK")')]
        for s in ('apply','review','fix','recheck'):task['checks'][s]=['COMBINED']
    h=Harness(project['config_path'],'ACTION-AGENT');ctx=start(h,task)
    plan={'kind':'git_merge','base_commit':base,'sources':sources} if kind=='git_merge' else None
    return h,ctx,plan,base


def resolve(out,worktree):
    p=deepcopy(out['context']['result_template'])
    assert p['stage_work']['phase']=='continue'
    Path(worktree,'src/double.py').write_text('def double(n):\n    return n * 2\n')
    p['stage_work']['resolutions']=[{'path':name,'reason':'Сохраняются обе эквивалентные реализации умножения.'} for name in out['action']['conflicts']]
    return p


def inspect(h,ctx,findings=None,decisions=None):
    return verify(h,result(ctx,{'coverage':'Проверены поведение и сохранность исходных изменений.',
         'findings':[] if findings is None else findings,'resolution_decisions':[] if decisions is None else decisions}))
