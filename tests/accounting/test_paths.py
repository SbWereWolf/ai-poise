from copy import deepcopy
from pathlib import Path
from conftest import DeterministicClock,write_json,git
from poise.runtime import Poise
from poise.application.work import WorkTools
from tests.runner.helpers import stage
from tests.batch.helpers import request
from .test_domain import policy,sample


def setup(project, clock=None):
    p={'goal_type':'development','worktree_required':True,'benefit':{'git_categories':['code','documentation'],'sections':[]},
       'route':{'entry':'write'},
       'stages':[stage('write','produce',{'complete':None},False,['src/**','tests/**','docs/**'],['write'])],
       'content_contract':{'sections':[],'routes':[],'requirements':[]}}
    project['cfg']['schema']='ddd-accounting-12';project['cfg']['accounting']=policy()
    project['cfg']['automatic_checks']=[]
    write_json(project['root']/'config/processes/development.json',p)
    write_json(project['config_path'],project['cfg'])
    t=deepcopy(project['task']);t['methods']=[];t['method_inputs']=[];t['checks']={'write':[]};t['evidence_plan']={'write':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
    t['stage_contracts']=[{'stage_id':'write','allowed_paths':['src/**','tests/**','docs/**'],
        'entry_requirements':[],'exit_requirements':[]}]
    t['decomposition']={**t['decomposition'],'phases':[{'stage':'write','skills':['task-domain'],'areas':[]}]}
    project['task']=deepcopy(t)
    h=Poise(project['config_path'],'A',clock=DeterministicClock() if clock is None else clock)
    w=WorkTools(h)
    out=w.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
    return h,w,out


def send(w,usage=(),cause=None,intervals=(),finding_targets=()):
    p=request('show',{'queries':[{'id':'t','kind':'task'}]})
    p['telemetry']={'usage':list(usage),'intervals':list(intervals),'cause':cause,'finding_targets':list(finding_targets)}
    return w.invoke(p)


def metrics(w,kind='all',id_=None,by=()):
    query={'id':'economics','kind':'accounting','scope':{'kind':kind,'id':id_},'group_by':list(by),'from':None,'to':None}
    return w.invoke(request('show',{'queries':[query]}))['results'][0]['value']


def settled_metrics(w,kind='all',id_=None,by=()):
    # Exact aggregate assertions require completed optional writes. Keep metrics()
    # nonblocking for the storage-isolation tests that deliberately hold a writer.
    w.runtime.telemetry.dispatcher.flush()
    return metrics(w,kind,id_,by)


def finish(w,out,tree):
    value=deepcopy(out['result_template']);value['sections']['report']='Done';value['commit_message']='feat: result'
    (tree/'src/double.py').write_text('def double(n):\n    return n * 2\n')
    (tree/'tests').mkdir(exist_ok=True);(tree/'tests/check.txt').write_text('test\n'*20)
    (tree/'docs').mkdir(exist_ok=True);(tree/'docs/result.md').write_text('Document\n')
    r=w.invoke(request('verify',{'result':value,'artifacts':[]}));assert r['status']=='verified'
    w.invoke(request('accept',{}));return r


def reopen_through_task_owner(h,w,feedback):
    # Accounting owner integration: terminal WorkTools selection does not reclaim.
    record=h.task_queries.record('T1')
    h.task_commands.rework('T1',h.session,feedback,
        git(Path(record['worktree']),'rev-parse','HEAD^{tree}'),'write')
    return w.invoke(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None}))


def test_missing_tokens_unknown_not_zero_and_messages_context(project):
    h,w,out=setup(project);r=settled_metrics(w,'task','T1')
    assert r['totals']['model_tokens'] is None
    assert r['totals']['token_coverage']=='unavailable'
    assert r['tasks'][0]['goal_type']=='development'


def test_usage_batch_dedup_conflict_and_atomicity(project):
    h,w,out=setup(project);send(w,[sample('x'),sample('y',sequence=2)]);send(w,[sample('x')])
    assert settled_metrics(w)['totals']['model_tokens']==240
    bad=sample('x',inp=300)
    result=send(w,[sample('new',sequence=3),bad])
    assert result['status']=='read_only'
    report=settled_metrics(w)
    assert report['totals']['model_tokens']==240
    assert report['telemetry']['coverage']=='partial' and report['telemetry']['failed']==1


def test_cumulative_baseline_and_details(project):
    h,w,out=setup(project)
    send(w,[sample('b','baseline',0,500,100),sample('n','cumulative',1,520,120)])
    m=settled_metrics(w)['totals'];assert m['model_tokens']==40 and m['cached_input_tokens']==10


def test_final_diff_credited_once_and_tests_excluded(project):
    h,w,out=setup(project);send(w,[sample()]);r=finish(w,out,Path(out['worktree']))
    m=settled_metrics(w,'task','T1');b=m['totals']['benefit']
    assert b['changed_lines']==3 # one replaced production line plus one doc line
    assert b['categories']['tests']['useful'] is False
    assert b['categories']['tests']['added_lines']==20
    assert b['changed_tokens'] is None
    h.task_commands.accept('T1',h.session,False,None)
    assert h.current_task() is None
    assert settled_metrics(w)['totals']['benefit']['changed_lines']==3


def test_reopen_revokes_benefit_cancel_cost_remains(project):
    h,w,out=setup(project);send(w,[sample()]);finish(w,out,Path(out['worktree']))
    reopen_through_task_owner(h,w,'Fix result')
    assert settled_metrics(w)['totals']['benefit']['changed_lines']==0
    send(w,[sample('fix',sequence=2)],cause='delivered_rework')
    w.invoke(request('cancel',{'reason':'User stops'}))
    m=settled_metrics(w)['totals'];assert m['model_tokens']==240 and m['cancelled_tokens']==240
    assert m['by_cause']['delivered_rework']['model_tokens']==120
    assert m['benefit']['changed_lines']==0


def test_recompletion_does_not_sum_old_and_new_payload(project):
    h,w,out=setup(project);finish(w,out,Path(out['worktree']))
    n=reopen_through_task_owner(h,w,'Update doc')
    p=deepcopy(n['result_template']);p['sections']['report']='Better';p['commit_message']='docs: update'
    (Path(n['worktree'])/'docs/result.md').write_text('Document\nSecond\n')
    w.invoke(request('verify',{'result':p,'artifacts':[]}));w.invoke(request('accept',{}))
    m=settled_metrics(w);assert m['totals']['benefit']['changed_lines']==4


def test_goal_type_and_calendar_groups_reconcile(project):
    h,w,out=setup(project);s=sample();s['occurred_at']='2026-09-06T23:59:00+00:00';send(w,[s])
    t=sample('second',sequence=2);t['occurred_at']='2026-09-07T00:01:00+00:00';send(w,[t])
    m=settled_metrics(w,by=['day','goal_type']);assert len(m['groups'])==2
    assert sum(g['model_tokens'] or 0 for g in m['groups'])==m['totals']['model_tokens']==240


def test_time_stops_at_report_not_user_wait(project):
    h,w,out=setup(project)
    finish(w,out,Path(out['worktree']))
    a=settled_metrics(w)['totals']['active_seconds']
    assert a is not None and a>0
    w.invoke(request('show',{'queries':[{'id':'t','kind':'task'}]}))
    assert settled_metrics(w)['totals']['active_seconds']==a
