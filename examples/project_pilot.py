"""Read-only first-stage pilot on an explicitly supplied, real Poise Git revision.

No synthetic application, no user acceptance, no target modifications. Project
configuration, task creation and result persistence use their existing tools.
The project-test command below is specific to this Poise pilot, not inferred
by the library for arbitrary applications.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from poise.common import load_config
from poise.composition import project_tools,goal_config_tools
from poise.infrastructure.catalogue import FileCatalogue
from poise.application.catalogue import CatalogueCommands
from poise.application.work import WorkTools
from poise.runtime import Poise


def run(root,repository,base_ref,destination,task_id,timeout):
    root=Path(root).resolve();repository=Path(repository).resolve()
    settings=root/'config/project-setup.json'
    setup=json.loads(settings.read_text());selected=setup['templates']['linux-reference']
    template=json.loads((root/selected['path']).read_text())
    values={'project':'poise-pilot','repository':str(repository),'base':base_ref,
            'remote':'not-configured','author_name':'Poise pilot','author_email':'pilot@example.invalid',
            'push':False,'state':'state','environment':['PATH','HOME']}
    request={'schema':'project-setup-1','request_id':task_id,'destination':destination,
             'template':{'id':'linux-reference','version':selected['version'],'digest':selected['digest']},
             'edits':[{'path':q['path'],'value':values[q['id']]} for q in template['questions']],
             'probe_repository':True}
    setup_result=project_tools(settings).apply(request)
    _,cfg,processes=load_config(Path(setup_result['config_path']))
    catalogue=FileCatalogue(root/'config/catalogue/settings.json')
    commands=CatalogueCommands(catalogue,goal_config_tools(catalogue.editor.path),catalogue.raw['max_items'])
    selected_task=catalogue.raw['task_templates']['verification-v1']
    invocation={'id':'VERIFY','argv':[sys.executable,'-m','pytest','tests/projects','-q'],
        'cwd':'.','environment':{'PYTHONPATH':'src'},'timeout_seconds':timeout,
        'expected_exit_code':0,'stdout_contains':['passed'],'stderr_contains':[]}
    parameters={'identity':task_id,'membership':None,
        'goal':'Plan the verification of declarative project setup on the real Poise revision.',
        'requirements':['The generated configuration loads all thirteen explicit process snapshots.',
                        'The exact setup test command works from the real Poise worktree.'],
        'dod':['Preserve the project receipt, exact test procedure and real readiness test output.',
               'Stop after the verified planning stage; do not invent user acceptance.'],
        'methods':[invocation],'artifact_requirements':[],
        'contract':{'sections':[],'routes':[],'requirements':[]},
        'evidence':{s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in processes['verification']['stages']}}
    parameters['evidence']['execution']['subject_methods']={
        'VERIFY':{'exit_codes':[0],'stdout_contains':['passed'],'stderr_contains':[]}}
    task=commands.tasks([{'template':{'id':'verification-v1','version':selected_task['version'],'digest':selected_task['digest']},
                           'parameters':parameters}],processes,cfg['automatic_checks'])['tasks'][0]
    h=Poise(setup_result['config_path'],task_id);work=WorkTools(h)
    def call(op,args):return work.invoke({'operation':op,'input':args,'messages':[]})
    context=call('bootstrap',{'task':task,'decision':None,'feedback':None,'rework_stage':None})
    before=subprocess.check_output(['git','-C',context['worktree'],'rev-parse','HEAD'],text=True).strip()
    result=deepcopy(context['result_template'])
    result['sections']={
       'report':'Prepared a concrete verification plan against the real Poise Git revision, not a generated toy application.',
       'verification_criteria':'C1: thirteen configurations load. C2: project setup tests pass from an isolated worktree. C3: no application changes or automatic stage acceptance.',
       'verification_program':'Run the explicitly registered PLANNING_READINESS command now; preserve stdout and the project receipt. VERIFY remains scheduled for the execution stage only after a new user instruction.',
       'verification_methods':json.dumps(invocation,ensure_ascii=False),
       'environment':json.dumps({'interpreter':sys.executable,'repository':str(repository),'revision':before,
                                 'worktree':context['worktree'],'network':'not required; push disabled explicitly'},ensure_ascii=False)}
    readiness={**invocation,'id':'PLANNING_READINESS'}
    result['method_additions']=[{'method':readiness,'stages':['planning']}]
    artifact={'scope':'task','path':'setup-receipt.json','source':{'kind':'text','text':json.dumps(setup_result,ensure_ascii=False,indent=2)}}
    report=call('verify',{'result':result,'artifacts':[artifact]})
    if report['status']!='verified':raise RuntimeError(json.dumps(report,ensure_ascii=False))
    replay=call('verify',{'result':result,'artifacts':[artifact]})
    state=h.current_task()
    dirty=subprocess.check_output(['git','-C',context['worktree'],'status','--porcelain'],text=True)
    if dirty or state['status']!='verified' or state['stage_index']!=0 or not replay.get('replayed'):
        raise RuntimeError('Read-only stage, stop boundary or replay did not hold')
    return {'status':'PASS','kind':'real_poise_first_stage_pilot','setup':setup_result,
            'target_revision':before,'worktree':context['worktree'],'task_id':task_id,'task_state':state['status'],
            'stage':'planning','user_acceptance':'not_performed','target_changed':bool(dirty),
            'token_usage':'unavailable','user_events':'not_injected','report':report,'repeat_replayed':replay['replayed']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--poise-root',type=Path,required=True)
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--base-ref',required=True)
    p.add_argument('--destination',required=True,help='new directory relative to Poise root')
    p.add_argument('--task-id',required=True)
    p.add_argument('--check-seconds',type=float,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.poise_root,a.repository,a.base_ref,a.destination,a.task_id,a.check_seconds),ensure_ascii=False,indent=2))
