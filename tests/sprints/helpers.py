from copy import deepcopy
import sys
from conftest import write_json
from batch.helpers import request


def policy():
    return {'max_tasks':1000,'max_dependencies':3000,
            'acceptable_terminal_states':['completed','cancelled'],
            'templates':{'basic':{'version':'1','goal':'','requirements':[],
                'definition_of_done':[], 'sections':{'plan':'Заполнить план.'},
                'tasks':[], 'dependencies':[]}},
            'section_rules':[{'name':'plan','template':'Заполнить план.',
                'normalization':'strip','required':True}]}


def setup(project):
    p=deepcopy(project['process'])
    stage=p['stages'][0]
    stage.update(id='work',instruction='Создать результат и подтвердить его.',
                 read_only=False,allowed_paths=['src/**','docs/**'],
                 transitions={'complete':None},rework_targets=['work'])
    p['stages']=[stage];p['route']['entry']='work'
    project['process']=p
    write_json(project['root']/'config/processes/development.json',p)
    docs=deepcopy(p);docs['goal_type']='documentation';docs['stages'][0]['allowed_paths']=['docs/**']
    write_json(project['root']/'config/processes/documentation.json',docs)
    cfg=project['cfg'];cfg['schema']='ddd-accounting-11';cfg['sprint']=policy()
    cfg['automatic_checks']=[]
    cfg['processes']['documentation']='config/processes/documentation.json'
    write_json(project['config_path'],cfg)
    return project


def task(project,identifier='A',kind='development',command='print("checked")'):
    t=deepcopy(project['task']);t.update(id=identifier,sprint_id='S',goal_type=kind,
        goal=f'Результат {identifier}',requirements=[f'R-{identifier}'],definition_of_done=[f'DOD-{identifier}'])
    t['methods']=[{'id':'CHECK','argv':[sys.executable,'-B','-c',command],'cwd':'.','environment':{},
                   'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},
                   'timeout_seconds':10,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}]
    t['method_inputs']=[{'method_id':'CHECK','repository_inputs':[],'future_outputs':[],
        'reference_profile':{'runner':'python','parser':'inline-no-path-arguments','version':1}}]
    t['checks']={'work':['CHECK']};t['evidence_plan']={'work':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
    return t


def changes(tasks,edges=()):
    return [{'kind':'purpose','goal':'Спринт проверки библиотек',
             'requirements':['SPR-R1'],'definition_of_done':['Все результаты проверены']},
            {'kind':'sections','values':{'plan':'Сначала независимые A/C, затем B с результатом A.'}},
            {'kind':'upsert_tasks','tasks':deepcopy(list(tasks))},
            {'kind':'dependencies','items':list(edges)}]


def draft(tools,tasks,edges=(),revision=None,request_id='draft-1',updates=None):
    return tools.invoke(request('sprint',{'action':'draft','sprint_id':'S',
        'request_id':request_id,'expected_revision':revision,
        'template':{'id':'basic','version':'1'} if revision is None else None,
        'changes':changes(tasks,edges) if updates is None else updates}))


def publish(tools,revision,request_id='publish-1'):
    return tools.invoke(request('sprint',{'action':'publish','sprint_id':None,
        'request_id':request_id,'expected_revision':revision}))


def bootstrap(tools,identifier=None):
    return tools.invoke(request('bootstrap',{'task':None if identifier is None else {'id':identifier},
                         'decision':None,'feedback':None,'rework_stage':None}))


def verify(tools,context,text='Результат завершён.'):
    p=deepcopy(context['result_template']);p['sections']['report']=text;p['commit_message']='test: sprint result'
    return tools.invoke(request('verify',{'result':p,'artifacts':[]}))


def publish_existing_contract(runtime, contract, sprint_id):
    """Test setup through the public domain application API, no raw task SQL."""
    t=deepcopy(contract);t['sprint_id']=sprint_id
    r=runtime.sprint_tools.apply({'action':'draft','sprint_id':sprint_id,'request_id':'fixture-draft',
       'expected_revision':None,'template':{'id':'basic','version':'1'},'changes':changes([t])})
    runtime.sprint_tools.apply({'action':'publish','sprint_id':sprint_id,'request_id':'fixture-publish','expected_revision':r['revision']})
    return t
