"""Reference route driver. User/reviewer decisions are fixtures, never model proof.

Production code contains no goal-name dispatch. This example deliberately supplies
13 different user goals, exact commands and edits to exercise that common code.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from demo import create, save, git, SOURCE
from poise.runtime import Poise
from poise.infrastructure.clock import SystemClock
from poise.application.work import WorkTools
from poise.application.catalogue import CatalogueCommands
from poise.infrastructure.catalogue import FileCatalogue
from poise.composition import goal_config_tools
from poise.common import load_config


EMPTY={'sections':[],'routes':[],'requirements':[]}


def coverage():
    reference=json.loads((SOURCE/'config/catalogue/reference.json').read_text())
    actual={(g['id'],s['id']) for g in reference['goal_types'] for s in g['stages']}
    expected={(n['goal_type'],n['stage']) for n in json.loads((SOURCE/'docs/architecture-design/data/stage-library-map.json').read_text())['nodes']}
    return {'nodes':len(actual),'feedback_edges':len(reference['feedback_edges']),
            'unmapped':sorted(expected-actual),'extra':sorted(actual-expected)}


def _selection(entry,ident):return {'id':ident,'version':entry['version'],'digest':entry['digest']}


def install(home):
    source=SOURCE/'config/catalogue'
    for directory in ('process-templates','task-templates'):
        shutil.copytree(source/directory,home/'config/catalogue'/directory)
    for name in ('settings.json','editor.json','reference.json'):
        shutil.copy2(source/name,home/'config/catalogue'/name)
    path=home/'config/catalogue/settings.json'
    repo=FileCatalogue(path)
    commands=CatalogueCommands(repo,goal_config_tools(repo.editor.path),repo.raw['max_items'])
    request=[{'goal_type':goal,'request_id':'install-'+goal,'mode':'create','expected_revision':None,
              'template':repo.process_selection(goal),'changes':[]} for goal in repo.editor.raw['processes']]
    result=commands.install(request)
    assert result['count']==13
    return repo,commands


def repository_method(mid,code,expected=0,needles=()):
    return {'id':mid,'argv':[sys.executable,'-B','-c',code],'cwd':'.','environment':{},
            'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},
            'expected_exit_code':expected,'stdout_contains':list(needles),'stderr_contains':[]}


def external_method(mid,code,expected=0,needles=()):
    return {'id':mid,'argv':[sys.executable,'-B','-c',code],'cwd':'.','environment':{},
            'source_under_test':{'kind':'external','reason':'The reference command reads only generated external state.'},
            'expected_exit_code':expected,'stdout_contains':list(needles),'stderr_contains':[]}


def _methods(goal,home):
    application=home.parent/'application'
    service=home/'service-state.json'
    inspect_service=f'from pathlib import Path; import json; p=Path({str(service)!r}); d=json.loads(p.read_text()); print(json.dumps(d,sort_keys=True))'
    perf='import time,json; a=time.perf_counter_ns(); value=sum(range(10000)); dt=time.perf_counter_ns()-a; assert value==49995000 and dt>0; print("MEASURED",json.dumps({"elapsed_ns":dt,"value":value}))'
    if goal=='development':
        baseline=f'import sys; sys.path.insert(0,{str(application)!r}); from src.double import double; assert double(2)==3; print("BASELINE=3")'
        methods=[external_method('BASELINE',baseline,needles=['BASELINE=3'])]
        argv=[sys.executable,'-B','-m','unittest','discover','-s','tests','-v']
        for mid,code,needles in [('TEST_RED',1,['test_double','AssertionError: 3 != 4','Ran 1 test']),('TEST_GREEN',0,['test_double','Ran 1 test','OK'])]:
            selected_argv=argv
            stdout=[];stderr=needles
            if mid=='TEST_RED':
                selected_argv=[sys.executable,'-B','-c',"import io,json,sys,unittest;result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.discover('tests'));print(json.dumps({'errors':sorted(case.id() for case,_ in result.errors),'failures':sorted(case.id() for case,_ in result.failures),'tests_run':result.testsRun},sort_keys=True,separators=(',',':')));raise SystemExit(0 if result.wasSuccessful() else 1)"]
                stdout=['{"errors":[],"failures":["test_double.Regression.test_double"],"tests_run":1}'];stderr=[]
            methods.append({'id':mid,'argv':selected_argv,'cwd':'.','environment':{'LANG':'C.UTF-8'},
                            'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},
                            'expected_exit_code':code,'stdout_contains':stdout,'stderr_contains':stderr})
        methods.append(repository_method('DOC_CHECK','from pathlib import Path; assert "double(2) == 4" in Path("docs/usage.md").read_text(); print("DOC_OK")',needles=['DOC_OK']))
        return methods
    if goal=='test_development':
        baseline=f'import sys; sys.path.insert(0,{str(application)!r}); from src.double import double; assert double(2)==4; print("BASELINE=4")'
        return [external_method('BASELINE',baseline,needles=['BASELINE=4']),
                {'id':'TEST_GREEN','argv':[sys.executable,'-B','-m','unittest','discover','-s','tests','-v'],'cwd':'.','environment':{},'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},'expected_exit_code':0,'stdout_contains':[],'stderr_contains':['Ran 1 test','OK']},
                repository_method('SENSITIVITY','import unittest; from unittest.mock import patch; from tests.test_double import Regression; s=unittest.TestSuite([Regression("test_double")]);\nwith patch("tests.test_double.double",lambda n:n+1):\n r=unittest.TestResult(); s.run(r)\nassert len(r.failures)==1 and not r.errors; print("DETECTED_BAD_IMPLEMENTATION")',needles=['DETECTED_BAD_IMPLEMENTATION'])]
    if goal=='verification':
        verify=f'import sys; sys.path.insert(0,{str(application)!r}); from src.double import double; print("VALUE="+str(double(2))); raise SystemExit(0 if double(2)==4 else 1)'
        return [external_method('VERIFY',verify,needles=['VALUE=4'])]
    if goal=='review':return []
    if goal=='design':return [repository_method('DESIGN_CHECK','from pathlib import Path; import json; x=json.loads(Path("docs/design.json").read_text()); assert x["operation"]=="double" and x["formula"]=="n * 2"; print("DESIGN_VALID")',needles=['DESIGN_VALID'])]
    if goal=='analysis':
        collect=f'import json; from pathlib import Path; v=json.loads(Path({str(application/'data/sample.json')!r}).read_text()); assert len(v)==3; print("RECORDS=3 SUM="+str(sum(v)))'
        return [external_method('COLLECT',collect,needles=['RECORDS=3 SUM=6'])]
    if goal=='profiling':
        return [external_method(i,perf+f'; print("PHASE={i}")',needles=['MEASURED',f'PHASE={i}'])
                for i in ('BASELINE','MEASURE','CONFIRM')]
    if goal=='environment_diagnostics':
        experiment=(f'from pathlib import Path; import json; p=Path({str(service)!r}); old=p.read_text();\ntry:\n p.write_text(json.dumps({{"ready":True}})); assert json.loads(p.read_text())["ready"]; print("CONTROLLED_CAUSE_CONFIRMED")\nfinally:\n p.write_text(old)')
        confirm=experiment.replace('CONTROLLED_CAUSE_CONFIRMED','CONTROLLED_FIX_CONFIRMED')
        return [external_method('REPRODUCE',inspect_service,needles=['"ready": false']),external_method('EXPERIMENT',experiment,needles=['CONTROLLED_CAUSE_CONFIRMED']),external_method('CONFIRM',confirm,needles=['CONTROLLED_FIX_CONFIRMED'])]
    if goal=='environment_remediation':return [external_method('BASELINE',inspect_service,needles=['"ready": false']),external_method('ENV_CHECK',inspect_service+'; assert d["ready"]',needles=['"ready": true'])]
    if goal=='documentation':return [repository_method('DOC_CHECK','from pathlib import Path; t=Path("docs/usage.md").read_text(); assert "double(2) == 4" in t and "Python" in t; print("DOC_OK")',needles=['DOC_OK'])]
    if goal=='sprint_planning':return [external_method('COVERAGE','print("CAPABILITY_READY: project fixture has a configured runtime and local Git")',needles=['CAPABILITY_READY'])]
    if goal=='task_planning':return []
    if goal=='integration':return [repository_method('COMBINED','from src.double import double; assert double(2)==4 and double(0)==0; print("COMBINED_OK")',needles=['COMBINED_OK'])]
    raise ValueError('Unknown reference scenario')


def _method_inputs(goal, methods):
    unittest_ids={'development':{'TEST_RED','TEST_GREEN'},'test_development':{'TEST_GREEN'}}.get(goal,set())
    return [{'method_id':m['id'],
             'repository_inputs':[],
             'future_outputs':[{'path':'tests','producer_stage':'test_implementation'}]
                 if m['id'] in unittest_ids and '-m' in m['argv'] else [],
             'reference_profile':{'runner':'unittest','parser':'discover-start-directory','version':1}
                 if m['id'] in unittest_ids and '-m' in m['argv'] else
                 {'runner':'python','parser':'inline-no-path-arguments','version':1}}
            for m in methods]


def _evidence(process,methods):
    ids={m['id']:m for m in methods};out={}
    checkers=[]
    for stage in process['stages']:
        sid=stage['id'];kind=stage['handler']
        out[sid]={'subject_methods':{},'arguments':[],'review_arguments':[]}
        if kind in ('observe','check'):
            checkers.append(sid)
    return out,ids,checkers


def prepare_task(repo,commands,processes,goal,task_id,home,membership=None):
    process=processes[goal];methods=_methods(goal,home)
    blueprint=repo.task_blueprint(_selection(repo.raw['task_templates'][goal+'-v1'],goal+'-v1'))
    # The template fixes scheduling. Only actual invocation/environment data comes from the caller.
    checks=blueprint.data['task']['checks']
    surfaces={
        ('development','TEST_RED'):['tests/**'],
        ('development','TEST_GREEN'):['src/**'],
        ('development','DOC_CHECK'):['docs/**'],
        ('test_development','TEST_GREEN'):['tests/**'],
        ('test_development','SENSITIVITY'):['tests/**'],
        ('design','DESIGN_CHECK'):['docs/**'],
        ('documentation','DOC_CHECK'):['docs/**'],
        ('integration','COMBINED'):['src/**'],
    }
    for method in methods:
        scheduled=[stage['id'] for stage in process['stages'] if method['id'] in checks[stage['id']]]
        red=scheduled if method['expected_exit_code'] else []
        failure=None
        if red:
            failure={'exit_code':method['expected_exit_code'],
                     'stdout_equals':'{"errors":[],"failures":["test_double.Regression.test_double"],"tests_run":1}\n',
                     'stderr_equals':''}
        method['verification_plan']={
            'responsibility':f'Verify {method["id"]} on its declared catalogue route stages.',
            'change_surface':[] if method['source_under_test']['kind']=='external' else surfaces[(goal,method['id'])],
            'red_stages':red,
            'green_stages':[] if red else scheduled,
            'red_failure':failure,
        }
    evidence,by_id,_=_evidence(process,methods)
    for stage in process['stages']:
        sid=stage['id'];mids=checks[sid]
        if stage['handler'] in ('observe','check'):
            for mid in mids:
                m=by_id[mid]
                # A negative product observation is a recognized procedure outcome.
                evidence[sid]['subject_methods'][mid]={'exit_codes':[0,1] if goal=='verification' else [m['expected_exit_code']],
                    'stdout_contains':['VALUE='] if goal=='verification' else [],'stderr_contains':[]}
        post=[x for x in process['content_contract']['sections'] if sid in x['write_stages']]
        if post and mids:
            evidence[sid]['arguments']=[{'id':'argument-'+sid,'kind':'logical','phase':'continue','observation_methods':mids}]
    for stage in process['stages']:
        if stage['handler']=='inspect':
            evidence[stage['id']]['review_arguments']=[a['id'] for sid,v in evidence.items() for a in v['arguments']]
    vals={'identity':task_id,'membership':membership,'goal':f'Reference {goal}: create and verify its declared result.',
          'requirements':['The declared reference result is supported by current observations.'],
          'dod':['The route finishes with retained evidence and reviewed result; no unrelated repository edits.'],
          'methods':methods,'method_inputs':_method_inputs(goal,methods),
          'artifact_requirements':[{'scope':'task','pattern':'artifacts/result.md','minimum':1,'maximum':1}],
          'contract':deepcopy(EMPTY),'evidence':evidence}
    if goal in ('development','test_development'):
        vals['executable_obligations']=['requirements[0]']
    return commands.tasks([{'template':_selection(repo.raw['task_templates'][goal+'-v1'],goal+'-v1'),
                             'parameters':vals}],processes,[])['tasks'][0]


def _seed(app,home,goal,scenario):
    if goal=='test_development':(app/'src/double.py').write_text('def double(n):\n    return n * 2\n')
    (app/'data').mkdir(exist_ok=True);save(app/'data/sample.json',[1,2,3])
    save(home/'service-state.json',{'ready':False,'revision':0})
    git(app,'add','.');git(app,'commit','-m','Reference fixture inputs');git(app,'push','backup','main')
    base=git(app,'rev-parse','HEAD');sources=[]
    if goal=='integration':
        for name in ('left','right'):
            git(app,'checkout','-b',name,base)
            if name=='right' and scenario=='short':
                (app/'docs').mkdir(exist_ok=True);(app/'docs/integration.md').write_text('The double operation returns n * 2.\n')
            else:(app/'src/double.py').write_text('def double(n):\n    return '+('n * 2' if name=='left' else 'n + n')+'\n')
            git(app,'add','.');git(app,'commit','-m','Existing '+name+' result')
            sources.append({'commit':git(app,'rev-parse','HEAD'),'checkpoint_message':'WIP join '+name})
        git(app,'checkout','main')
    return base,sources


def _environment_plan(home,revision):
    path=str(home/'service-state.json');desired={'ready':True,'revision':revision}
    probe=external_method('PROBE',f'from pathlib import Path; import json; assert json.loads(Path({path!r}).read_text())=={desired!r}; print("READY")',needles=['READY'])
    apply=external_method('APPLY',f'from pathlib import Path; import json; Path({path!r}).write_text(json.dumps({desired!r})); print("APPLIED")',needles=['APPLIED'])
    return {'kind':'commands','steps':[{'id':'service-state','apply':apply,'probe':probe,'probe_false_exit_codes':[1]}]}


def run(directory,goal,scenario,feedback_edge=None):
    directory=Path(directory).resolve();home=create(directory);app=directory/'application'
    repo,commands=install(home)
    cfg=json.loads((home/'project.json').read_text());cfg['processes']=deepcopy(repo.editor.raw['processes']);cfg['automatic_checks']=[]
    save(home/'project.json',cfg)
    _,_,processes=load_config(home/'project.json');process=processes[goal]
    base,sources=_seed(app,home,goal,scenario)
    task=prepare_task(repo,commands,processes,goal,'MAIN',home)
    children=[];sprint_body=None
    if goal in ('task_planning','sprint_planning'):
        sid=None if goal=='task_planning' else 'PUBLISHED-SPRINT'
        children=[prepare_task(repo,commands,processes,'review','CHILD-A',home,sid)]
        if sid is not None:
            children.append(prepare_task(repo,commands,processes,'review','CHILD-B',home,sid))
            sprint_body={'sprint_id':sid,'template':{'id':'basic','version':'1'},'changes':[
                {'kind':'purpose','goal':'Review two scoped results.','requirements':['Inspect both results.'],'definition_of_done':['Two valid review tasks can start in dependency order.']},
                {'kind':'sections','values':{'plan':'Review CHILD-A first; CHILD-B then uses the accepted review context.'}},
                {'kind':'upsert_tasks','tasks':children},
                {'kind':'dependencies','items':[{'predecessor':'CHILD-A','successor':'CHILD-B','kind':'completion'}]}]}
    h=Poise(home/'project.json','CATALOGUE-AGENT',SystemClock());tool=WorkTools(h);calls=[]
    def invoke(op,args):
        out=tool.invoke({'operation':op,'input':args,'messages':[]})
        calls.append({'operation':op,'status':out['status'],'stage':out.get('stage')})
        return out
    def boot(decision=None,target=None):
        return invoke('bootstrap',{'task':None,'decision':decision,'feedback':'User: return to the indicated stage and address this finding.' if target else None,'rework_stage':target})
    ctx=invoke('bootstrap',{'task':task,'decision':None,'feedback':None,'rework_stage':None})
    ref=json.loads((SOURCE/'config/catalogue/reference.json').read_text())
    edges=[e for e in ref['feedback_edges'] if e['goal_type']==goal]
    selected={}
    if scenario=='feedback':
        selected={e['from_stage']:e['to_stage'] for e in reversed(edges)}
        if feedback_edge is not None:
            e=next(e for e in edges if e['id']==feedback_edge);selected={e['from_stage']:e['to_stage']}
            # Enter a remediation branch through its existing first inspection.
            nodes={n['id']:n for n in process['stages']};happy=set();cur=process['route']['entry']
            while cur is not None and cur not in happy:
                happy.add(cur);n=nodes[cur]
                outcome='clear' if n['handler']=='inspect' else ('satisfied' if n['handler']=='check' else 'complete')
                cur=n['transitions'][outcome]
            if e['from_stage'] not in happy:
                entry=next(x for x in edges if x['from_stage'] in happy and x['to_stage']==e['to_stage'])
                selected[entry['from_stage']]=entry['to_stage']
    counts={};reports=[];accepted=[];traversed=[];read_only_checks=0;feedback_cycles=0;counter=0
    while True:
        counter+=1
        sid=ctx['stage'];stage=next(s for s in process['stages'] if s['id']==sid);kind=stage['handler']
        counts[sid]=counts.get(sid,0)+1;wt=Path(ctx['worktree']);before=git(wt,'rev-parse','HEAD^{tree}')
        current=ctx['workflow']['feedback'];pending=current['pending_resolutions'];open_findings=current['open_findings']
        # Source-code/fixture editing is scenario work, not Poise bookkeeping.
        if goal in ('development','test_development') and sid in ('test_implementation','test_remediation','remediation'):
            (wt/'tests').mkdir(exist_ok=True)
            (wt/'tests/__init__.py').write_text('')
            (wt/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2), 4)\n')
        if goal=='development' and sid in ('implementation','implementation_remediation'):
            (wt/'src/double.py').write_text('def double(n):\n    return n * 2\n')
        if (goal=='development' and sid=='documentation') or (goal=='documentation' and sid in ('drafting','remediation')):
            (wt/'docs').mkdir(exist_ok=True);(wt/'docs/usage.md').write_text('# Double\n\nRequires Python. The contract is double(2) == 4.\n')
        if goal=='design' and sid in ('detailed_design','remediation'):
            (wt/'docs').mkdir(exist_ok=True);save(wt/'docs/design.json',{'operation':'double','formula':'n * 2'})
        p=deepcopy(ctx['result_template'])
        for name in stage['sections']:
            if name=='planned_task':p['sections'][name]=json.dumps(children,ensure_ascii=False)
            elif name=='planned_sprint':p['sections'][name]=json.dumps(sprint_body,ensure_ascii=False)
            elif name=='test_registry':p['sections'][name]=json.dumps({'methods':[m['id'] for m in task['methods']],'checks':task['checks']})
            elif name=='product_findings':p['sections'][name]='The supplied product has double(2)=3 rather than 4. This is a result of review, not an internal blocker.'
            else:p['sections'][name]=f'{goal}.{sid}, approach {counts[sid]}: {name}. The reference fixture records explicit inputs, exact methods and a review of the declared result; this text is authored by the scenario, not a model quality evaluation.'
        for extra in process['content_contract']['sections']:
            if sid in extra['write_stages'] and not task['checks'][sid]:
                p['sections'][extra['id']]='Known fixture input: the service-state file is initially ready=false; reproduction follows as its own exact command. No new command result is claimed.'
        if goal in ('development','test_development') and sid=='test_implementation':
            method=next(item for item in task['methods'] if item['id']=='TEST_GREEN')
            scheduled=[stage['id'] for stage in process['stages'] if method['id'] in task['checks'][stage['id']]]
            p['method_additions']={
                'request_id':f'{goal}-current-registry-1',
                'expected_revision':0,
                'operations':[{'kind':'replace','method_id':method['id'],'registration':{
                    'method':method,'stages':scheduled,'evidence_kind':'executable_test',
                    'covers':['requirements[0]'],
                }}],
                'executable_obligations':['requirements[0]'],
            }
        p['commit_message']='catalogue: '+goal+' '+sid
        # A proposal may be made only for already delivered open findings and is
        # accepted later. It does not silently mark the original problem solved.
        proposals=[{'id':f'R-{counter}-{f["id"]}','finding_id':f['id'],
                    'description':'Re-executed the requested part and corrected the retained result.',
                    'evidence':f'Current sections and command receipts of {sid} approach {counts[sid]}.'} for f in open_findings] if open_findings and not pending else []
        trigger=sid in selected and counts[sid]==1
        if kind=='inspect':
            decisions=[{'resolution_id':r['id'],'decision':'accepted','reason':'The fixture explicitly reviews and accepts the new result.'} for r in pending]
            findings=[{'id':'F-'+sid,'subject':f'{goal} preceding delivered result','description':'One relevant case/evidence link requires a new approach.',
                       'evidence':'The following fixture asks for a documented correction and a subsequent decision.'}] if trigger else []
            p['stage_work']={'coverage':'Inspected the current target and relevant evidence; the fixture supplies this reviewer decision.',
                             'findings':findings,'resolution_decisions':decisions}
            if proposals:
                p['stage_work']['resolutions']=proposals
                p['stage_work']['findings']=[]
                trigger=False
        elif kind=='revise':p['stage_work']={'resolutions':proposals}
        elif kind=='apply_plan':
            plan={'kind':'git_merge','base_commit':base,'sources':sources} if goal=='integration' else _environment_plan(home,2 if sid=='correction' else 1)
            p['stage_work']={'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':proposals}
        elif kind=='publish':
            p['stage_work']=({'kind':'tasks','section':'planned_task','authorization':'User: publish the reviewed child contracts.'} if goal=='task_planning' else
                             {'kind':'sprint','section':'planned_sprint','authorization':'User: publish the reviewed sprint.'} if goal=='sprint_planning' else
                             {'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish the accepted integrated result.'})
        else:p['stage_work']={'resolutions':proposals} if proposals else {}
        # Review only argument versions that actually exist, never future outputs.
        arg_plan=task['evidence_plan'][sid]
        # The public evidence view contains complete current arguments and decisions.
        proof={'arguments':[],'decisions':[]}
        if arg_plan['review_arguments']:
            view=invoke('show',{'queries':[{'id':'proof','kind':'evidence'}]})['results'][0]['value']
            proof=view['book'] if 'book' in view else view
        arguments=proof['arguments']
        review=[]
        for aid in arg_plan['review_arguments']:
            matching=[a for a in arguments if a['id']==aid]
            if matching:
                latest=matching[-1]
                review.append({'argument_id':aid,'revision':latest['revision'],'decision':'accepted','reason':'Fixture reviewer checked observations and bounded inference.'})
        p['evidence_work']={'phase':'prepare','arguments':[],'decisions':review}
        artifact=[{'scope':'task','path':'result.md','source':{'kind':'text','text':f'Reference result of {goal}. Evidence and reviewer decisions are retained in this task.\n'}}]
        out=invoke('verify',{'result':p,'artifacts':artifact})
        if out['status']=='awaiting_action_continuation':
            p=deepcopy(out['context']['result_template'])
            (wt/'src/double.py').write_text('def double(n):\n    return n * 2\n')
            p['stage_work']['resolutions']=[{'path':x,'reason':'Both source tasks require multiplication by two; preserve that combined behaviour.'} for x in out['action']['conflicts']]
            out=invoke('verify',{'result':p,'artifacts':[]})
        if out['status']=='awaiting_continuation':
            receipt_ids=[r['id'] for r in out['checks']]
            p=deepcopy(out['context']['result_template'])
            for extra in process['content_contract']['sections']:
                if sid in extra['write_stages']:
                    p['sections'][extra['id']]='Observed receipts confirm the explicitly scoped fact. No claim beyond this fixture experiment is made.'
            p['evidence_work']={'phase':'continue','arguments':[{'id':a['id'],'kind':a['kind'],
                'facts':['The current retained command observation completed and is referenced below.'],
                'assumptions':[],'inference':'This argument concerns only the measured/checked fixture state.',
                'conclusion':'The procedure produced the stated observation.','verdict':'proved','observation_ids':receipt_ids}
                for a in arg_plan['arguments'] if a['phase']=='continue'],'decisions':review}
            out=invoke('verify',{'result':p,'artifacts':[]})
        assert out['status']=='verified',(goal,sid,out)
        if stage['read_only']:
            assert git(wt,'rev-parse','HEAD^{tree}')==before
            read_only_checks+=1
        reports.append({'stage':sid,'iteration':ctx['iteration'],'outcome':out['stage_outcome'],
                        'checks':[{'id':x['id'],'exit_code':x['actual_exit_code'],'passed':x['passed']} for x in out['checks']]})
        accepted.append(sid)
        if trigger:
            feedback_cycles+=1
            target=selected[sid]
            traversed.extend(e['id'] for e in edges if (e['from_stage'],e['to_stage'])==(sid,target))
            # The user explicitly chooses an allowed feedback target in this cycle.
            ctx=boot('rework',target)
            assert ctx['stage']==target
            continue
        if h.runner.context(task['id'])['next_stage'] is None:
            final=invoke('accept',{});break
        ctx=boot('continue')
    assert final['status']=='completed'
    pub_tasks=[];child_valid=False
    if children:
        pub_tasks=[c['id'] for c in children]
        assert all(h.task_queries.record(c)['status']=='available' for c in pub_tasks)
        start_child=invoke('bootstrap',{'task':{'id':pub_tasks[0]},'decision':None,'feedback':None,'rework_stage':None})
        child_valid=start_child['stage']==processes['review']['route']['entry']
    remote=directory/'remote.git'
    current_target=git(remote,'rev-parse','refs/heads/main')
    artifacts=final['artifacts']
    output={'status':'PASS','goal_type':goal,'scenario':scenario,'feedback_edge':feedback_edge,
            'task_status':'completed','reports':reports,'accepted_stages':accepted,
            'feedback_cycles':feedback_cycles,'traversed_feedback_edges':traversed,
            'open_findings_at_completion':len(final['feedback']['open_findings']),
            'read_only_checks':read_only_checks,
            'ready_artifacts':sum(Path(a['path']).is_file() for a in artifacts),
            'published_tasks':pub_tasks,'child_bootstrap_valid':child_valid,
            'target_published':current_target!=base,'target_unchanged':current_target==base,
            'calls':calls,'notice':'User/reviewer arguments are explicit test fixtures; commands, Git and SQLite are real.'}
    if goal=='environment_diagnostics':assert json.loads((home/'service-state.json').read_text())=={'ready':False,'revision':0}
    if goal=='environment_remediation':assert json.loads((home/'service-state.json').read_text())['ready']
    save(directory/'route-report.json',output)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--goal',required=True)
    parser.add_argument('--scenario',required=True,choices=['short','feedback'])
    args=parser.parse_args()
    out=run(args.directory,args.goal,args.scenario)
    print(json.dumps({k:v for k,v in out.items() if k not in ('calls','reports')},ensure_ascii=False,indent=2))
