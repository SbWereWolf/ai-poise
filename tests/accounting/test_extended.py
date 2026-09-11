from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import pytest
from conftest import write_json
from poise.common import PoiseError
from poise.runtime import Poise
from poise.application.work import WorkTools
from tests.batch.helpers import request,message
from .test_domain import sample
from .test_paths import setup,send,metrics,finish


def test_usage_transaction_rolls_back_all_new_events(project):
    h,w,c=setup(project)
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON accounting_usage WHEN NEW.sequence=2 BEGIN SELECT RAISE(ABORT,'injected accounting failure'); END")
    with pytest.raises(sqlite3.IntegrityError):send(w,[sample('a'),sample('b',sequence=2)])
    assert metrics(w)['totals']['model_tokens'] is None


def test_exact_measurement_tokenizer_and_no_conversion_to_model_usage(project,tmp_path):
    h,w,c=setup(project)
    # This is an explicit deterministic fixture instrument, NOT an LLM tokenizer.
    script=tmp_path/'count.py';script.write_text('import sys,json\nx=json.load(sys.stdin)\njson.dump({"counts":[len(t) for t in x["texts"]]},sys.stdout)\n')
    project['cfg']['accounting']['tokenizer']={'kind':'command','identity':'fixture-codepoint-v1','command':{
        'argv':[sys.executable,str(script)],'cwd':str(tmp_path),'environment':{},'timeout_seconds':5,'max_output_bytes':1024}}
    # New task/store is needed because the original measurement contract is immutable.
    project['cfg']['paths']['state']='tokenized-state';write_json(project['config_path'],project['cfg'])
    h2=Poise(project['config_path'],'tok');t=deepcopy(project['task']);t['id']='TOKEN'
    t['methods']=[];t['checks']={'write':[]};t['evidence_plan']={'write':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
    w2=WorkTools(h2);c=w2.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
    finish(w2,c,Path(c['worktree']))
    r=metrics(w2);assert r['totals']['benefit']['changed_tokens']==len('    return n + 1\n    return n * 2\nDocument\n')
    assert r['totals']['model_tokens'] is None


def test_tokenizer_error_keeps_business_completion_and_known_byte_measure(project,tmp_path):
    h,w,c=setup(project)
    # Inject a failure at the measurement port, without changing the captured policy.
    h.accounting.port.measurer.tokenize=lambda texts: (_ for _ in ()).throw(PoiseError('instrument unavailable'))
    finish(w,c,Path(c['worktree']))
    assert h.current_task()['status']=='completed'
    r=metrics(w)
    assert r['totals']['benefit']['changed_lines']==3
    assert r['totals']['benefit']['changed_tokens'] is None
    assert r['tasks'][0]['benefit']['tokenizer_error']=='instrument unavailable'


def test_reported_intervals_split_dates_and_do_not_include_user_wait(project):
    h,w,c=setup(project)
    # Select the reported mode explicitly in a fresh instance/store.
    project['cfg']['accounting']['time_mode']='reported';project['cfg']['paths']['state']='reported-state'
    write_json(project['config_path'],project['cfg'])
    t=deepcopy(project['task']);t['id']='TIME';t['methods']=[];t['checks']={'write':[]};t['evidence_plan']={'write':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
    w=WorkTools(Poise(project['config_path'],'S'))
    w.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
    span={'source':'test','stream':'clock','event_id':'i1','started_at':'2026-09-06T23:59:00Z','ended_at':'2026-09-07T00:01:00Z'}
    send(w,intervals=[span]);send(w,intervals=[span])
    m=metrics(w,by=['day']);assert m['totals']['active_seconds']==120
    assert [g['active_seconds'] for g in m['groups']]==[60,60]
    with pytest.raises(PoiseError):send(w,intervals=[{**span,'event_id':'overlap'}])


def test_two_parallel_agents_sum_time_but_union_elapsed(project):
    h,w,c=setup(project);project['cfg']['accounting']['time_mode']='reported';project['cfg']['paths']['state']='parallel-state'
    write_json(project['config_path'],project['cfg'])
    for actor in ('A','B'):
        t=deepcopy(project['task']);t['id']='TIME'+actor;t['methods']=[];t['checks']={'write':[]};t['evidence_plan']={'write':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
        tools=WorkTools(Poise(project['config_path'],actor))
        tools.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
        send(tools,intervals=[{'source':'test','stream':actor,'event_id':'i','started_at':'2026-09-07T10:00:00Z','ended_at':'2026-09-07T11:00:00Z'}])
    r=metrics(tools);assert r['totals']['active_seconds']==7200
    assert r['totals']['wall_active_seconds']==3600


def test_task_sections_measure_only_selected_final_content(project):
    h,w,c=setup(project)
    p=deepcopy(project['process']) # actual on-disk route has write
    p=json.loads((project['root']/'config/processes/development.json').read_text())
    p['benefit']={'git_categories':[],'sections':['report']}
    write_json(project['root']/'config/processes/development.json',p)
    project['cfg']['paths']['state']='sections-state';write_json(project['config_path'],project['cfg'])
    t=deepcopy(project['task']);t['id']='SECTION';t['methods']=[];t['checks']={'write':[]};t['evidence_plan']={'write':{'subject_methods':{},'arguments':[],'review_arguments':[]}}
    w=WorkTools(Poise(project['config_path'],'A'));c=w.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
    v=deepcopy(c['result_template']);v['sections']['report']='Итог\n';v['commit_message']='docs: result'
    w.invoke(request('verify',{'result':v,'artifacts':[]}));w.invoke(request('accept',{}))
    b=metrics(w)['totals']['benefit'];assert b['changed_lines']==1 and b['changed_bytes']==9


def test_missing_policy_and_benefit_do_not_get_guessed(project):
    from poise.modules.goal_config.domain import GoalTypeDefinition
    h,w,c=setup(project);p=json.loads((project['root']/'config/processes/development.json').read_text());del p['benefit']
    with pytest.raises(PoiseError):GoalTypeDefinition.parse(p)
    del project['cfg']['accounting'];write_json(project['config_path'],project['cfg'])
    with pytest.raises(PoiseError):Poise(project['config_path'],'B')


def test_benefit_is_editable_in_same_declarative_config_batch(project):
    from poise.modules.goal_config.domain import GoalTypeDefinition
    h,w,c=setup(project);p=json.loads((project['root']/'config/processes/development.json').read_text())
    r=GoalTypeDefinition.build('development',p,[{'op':'set_benefit','value':{'git_categories':[],'sections':['report']}}])
    assert r.data['benefit']['sections']==['report']


def test_metrics_survive_transfer_and_retry_without_double_counting(project,tmp_path):
    from tests.transfer.helpers import destination,export,restore,pick,handoff_args
    h,w,c=setup(project);send(w,[sample()],cause='initial')
    p=deepcopy(c['result_template']);p['sections']['report']='WIP';p['commit_message']='WIP: save'
    saved=export(w,handoff=handoff_args(p))
    dst=destination(project,tmp_path/'receiver');other=WorkTools(Poise(dst['config_path'],'B'))
    restore(other,saved['package_path'],saved['package_digest']);pick(other,'T1')
    send(other,[sample()])
    assert metrics(other)['totals']['model_tokens']==120
    restore(other,saved['package_path'],saved['package_digest'])
    assert metrics(other)['totals']['model_tokens']==120


def test_user_messages_all_count_once_in_calendar_and_goal_views(project):
    h,w,c=setup(project)
    for m in [message('u1','initial'),message('u2','continue'),message('u3',None),message('u1','initial')]:
        w.invoke(request('show',{'queries':[{'id':'x','kind':'task'}]},[m]))
    r=metrics(w,by=['goal_type']);assert r['totals']['user_messages_count']==3
    assert r['groups'][0]['observed_messages_count']==3


def test_new_user_turn_does_not_charge_wait_after_failed_verify(project):
    h,w,c=setup(project)
    # Close setup interval and use explicit test clock for a new work session.
    h.accounting.close_cycle()
    now=['2026-09-07T10:00:00+00:00'];h.accounting.port.clock=lambda:now[0]
    w.invoke(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None},[message('turn-a')]))
    p=deepcopy(c['result_template']);p['sections']['report']='Attempt';p['commit_message']='feat: measured'
    p['method_additions']=[{'method':{'id':'FAIL','argv':[sys.executable,'-c','raise SystemExit(1)'],'cwd':'.','environment':{},'timeout_seconds':5,'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]},'stages':['write']}]
    now[0]='2026-09-07T10:01:00+00:00'
    assert w.invoke(request('verify',{'result':p,'artifacts':[]}))['status']=='checks_failed'
    base=metrics(w)['totals']['active_seconds'] or 0
    now[0]='2026-09-07T20:00:00+00:00'
    w.invoke(request('cancel',{'reason':'User stops'},[message('turn-b','cancel')]))
    total=metrics(w)['totals']['active_seconds']
    assert 60<=total-base<61


def test_tool_cycle_clamps_host_clock_rollback_without_aborting_work(project):
    h,w,c=setup(project)
    h.accounting.close_cycle()
    observed='2030-01-01T10:00:00+00:00'
    h.accounting.port.clock=lambda:observed
    send(w)

    resumed=Poise(project['config_path'],'A')
    resumed.accounting.port.clock=lambda:'2030-01-01T09:00:00+00:00'
    resumed_work=WorkTools(resumed)
    send(resumed_work)
    resumed_work.invoke(request('cancel',{'reason':'Fixture complete'}))

    with resumed.store.transaction() as db:
        row=db.execute(
            "SELECT started_at,ended_at,data FROM accounting_cycles "
            "WHERE session_id='A' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    data=json.loads(row['data'])
    assert row['started_at']==observed
    assert row['ended_at']==observed
    assert data['last_observed_at']==observed
    assert data['seconds']==0
