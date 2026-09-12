"""Real SQLite, Git and commands. User/reviewer decisions are explicit fixtures."""
import json
import sqlite3
import sys
from pathlib import Path
import pytest
from conftest import write_json, fill
from conftest import Poise
from poise.common import PoiseError
from runner.helpers import stage


def setup(project, kind='observe', logical=True, phase='continue', negative=False):
    stages=[stage('measure',kind, {'complete':'audit'} if kind=='observe' else
                 {'satisfied':'audit','not_satisfied':'audit','inconclusive':'audit'},True,[],['measure']),
            stage('audit','inspect',{'clear':None,'changes_requested':'measure'},True,[],['audit','measure'])]
    proc={'goal_type':'verification_demo','benefit':{'git_categories':[], 'sections':[]},'route':{'entry':'measure'},
          'content_contract':{'sections':[],'routes':[],'requirements':[]},'stages':stages}
    write_json(project['root']/'config/processes/verification_demo.json',proc)
    cfg=project['cfg']; cfg['processes']={'verification_demo':'config/processes/verification_demo.json'}
    cfg['automatic_checks']=[]
    write_json(project['config_path'],cfg)
    counter=project['root']/'calls.txt'
    code=f"from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('observed=3'); raise SystemExit({1 if negative else 0})"
    m={'id':'M','argv':[sys.executable,'-B','-c',code], 'cwd':'.','environment':{},
       'source_under_test':{'kind':'external','reason':'The observer records generated evidence and reads no repository source.'},
       'expected_exit_code':0,'stdout_contains':['observed=3'],'stderr_contains':[]}
    task=project['task']; task.update(goal_type='verification_demo', methods=[m],
       method_inputs=[{'method_id':'M','repository_inputs':[],'future_outputs':[],
           'reference_profile':{'runner':'python','parser':'inline-no-path-arguments','version':1}}],
       checks={'measure':['M'],'audit':[]},
       evidence_plan={'measure':{'subject_methods':{'M':{'exit_codes':[0,1],'stdout_contains':['observed=3'],'stderr_contains':[]}},'arguments':[
           {'id':'A','kind':'logical','phase':phase,'observation_methods':['M'] if phase=='continue' else []}] if logical else [],'review_arguments':[]},
           'audit':{'subject_methods':{},'arguments':[],'review_arguments':['A'] if logical else []}})
    write_json(project['task_path'],task)
    return Poise(project['config_path'],'S1'), counter


def input_result(context, arguments=(), decisions=(), phase='prepare'):
    p=fill(context,'Работа этапа выполнена.')
    data=p
    data['evidence_work']={'phase':phase,'arguments':list(arguments),'decisions':list(decisions)}
    if context['handler']=='inspect':
        data['stage_work']={'coverage':'Осмотрено доказательство по критерию.', 'findings':[],'resolution_decisions':[]}



def arg(observation_ids, inference='По определению наблюдаемый результат подтверждает критерий.'):
    return {'id':'A','kind':'logical','facts':['Наблюдение содержит observed=3.'], 'assumptions':[],
            'inference':inference,'conclusion':'Критерий подтверждён.', 'verdict':'proved','observation_ids':observation_ids}


def test_observe_continue_restart_no_rerun_and_accept(project):
    h,counter=setup(project)
    ctx=h.bootstrap(project['task_path']); input_result(ctx)
    pending=h.verify()
    assert pending['status']=='awaiting_continuation'
    assert h.show()['status']=='active' and counter.read_text()=='x'
    ids=[r['id'] for r in pending['checks']]
    input_result(pending['context'],[arg(ids)],phase='continue')
    h=Poise(project['config_path'],'S1')
    report=h.verify()
    assert report['status']=='verified' and counter.read_text()=='x'
    assert report['evidence']['arguments'][0]['body']['facts']
    assert report['evidence']['decisions']==[]
    assert h.verify()['replayed'] and counter.read_text()=='x'
    assert not Path(ctx['runtime_root']).exists()
    assert all(Path(r['stdout']).is_file() for r in report['checks'])
    review=h.bootstrap(decision='continue')
    proof=report['evidence']['arguments'][0]
    input_result(review,decisions=[{'argument_id':'A','revision':proof['revision'],'decision':'accepted','reason':'Аргумент осмотрен.'}])
    assert h.verify()['stage_outcome']=='clear'
    assert h.accept()['status']=='completed'


def test_check_negative_product_completes_successfully(project):
    h,counter=setup(project,kind='check',logical=False,negative=True)
    ctx=h.bootstrap(project['task_path']); input_result(ctx)
    result=h.verify()
    assert result['status']=='verified'
    assert result['stage_outcome']=='not_satisfied' and result['checks'][0]['passed'] is False
    assert counter.read_text()=='x'


def test_guard_failure_is_not_swallowed_by_observe(project):
    h,counter=setup(project,logical=False)
    task=project['task']; guard={**task['methods'][0],'id':'GUARD','argv':[sys.executable,'-c','raise SystemExit(1)'],'stdout_contains':[]}
    task['methods'].append(guard); task['method_inputs'].append({'method_id':'GUARD','repository_inputs':[],
        'future_outputs':[],'reference_profile':{'runner':'python','parser':'inline-no-path-arguments','version':1}})
    task['checks']['measure'].append('GUARD'); write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1'); ctx=h.bootstrap(project['task_path']); input_result(ctx)
    result=h.verify()
    assert result['status']=='checks_failed'
    assert h.show()['status']=='active'


def test_logical_only_has_no_command_receipts(project):
    h,counter=setup(project,kind='check',phase='prepare')
    task=project['task']; task['methods']=[]; task['method_inputs']=[]; task['checks']['measure']=[]; task['evidence_plan']['measure']['subject_methods']={}
    write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1'); ctx=h.bootstrap(project['task_path'])
    input_result(ctx,[arg([])])
    result=h.verify()
    assert result['status']=='verified' and result['stage_outcome']=='satisfied'
    assert result['checks']==[] and not counter.exists()


def test_missing_pre_argument_blocks_command_before_execution(project):
    h,counter=setup(project,phase='prepare')
    ctx=h.bootstrap(project['task_path']); input_result(ctx)
    result=h.verify()
    assert result['status']=='evidence_requirements_failed'
    assert not counter.exists()


def test_rejected_proof_then_new_iteration_preserves_old_records(project):
    h,counter=setup(project)
    ctx=h.bootstrap(project['task_path']); input_result(ctx); pending=h.verify()
    input_result(pending['context'],[arg([pending['checks'][0]['id']])],phase='continue')
    result=h.verify(); old=result['evidence']['arguments'][0]
    ctx=h.bootstrap(decision='continue')
    input_result(ctx,decisions=[{'argument_id':'A','revision':old['revision'],'decision':'rejected','reason':'Не раскрыто допущение.'}])
    report=h.verify(); assert report['stage_outcome']=='changes_requested'
    ctx=h.bootstrap(decision='continue'); assert ctx['stage']=='measure' and ctx['iteration']==2
    input_result(ctx); pending=h.verify(); assert counter.read_text()=='xx'
    input_result(pending['context'],[arg([pending['checks'][0]['id']],inference='Добавлено обоснование допущения.')],phase='continue')
    result=h.verify(); new=result['evidence']['arguments'][-1]; assert new['revision']!=old['revision']
    ctx=h.bootstrap(decision='continue')
    input_result(ctx,decisions=[{'argument_id':'A','revision':new['revision'],'decision':'accepted','reason':'Все предпосылки проверены.'}])
    report=h.verify(); assert report['stage_outcome']=='clear'
    assert len(report['evidence']['arguments'])==2 and len(report['evidence']['decisions'])==2
    assert h.accept()['status']=='completed'


def test_changed_observation_input_rejects_stale_argument(project, monkeypatch):
    h,counter=setup(project); ctx=h.bootstrap(project['task_path']); input_result(ctx); pending=h.verify()
    input_result(pending['context'],[arg([pending['checks'][0]['id']])],phase='continue')
    monkeypatch.setenv('LANG','changed-explicit-input')
    result=h.verify()
    assert result['status']=='observations_stale' and h.show()['status']=='active'
    assert counter.read_text()=='x'


def test_task_api_cannot_mark_verified_before_evidence_gate(project):
    h,_=setup(project); ctx=h.bootstrap(project['task_path']); input_result(ctx)
    payload=ctx['result_template']
    rec=h.runner.submit('T1','S1',payload)
    with pytest.raises(PoiseError):
        h.task_commands.mark_verified('T1','S1',rec.digest,{'verified_tree':'anything'},())
    assert h.show()['status']=='active'


def test_evidence_transaction_rolls_back_all_business_records(project):
    h,counter=setup(project); ctx=h.bootstrap(project['task_path']); input_result(ctx); pending=h.verify()
    input_result(pending['context'],[arg([pending['checks'][0]['id']])],phase='continue')
    payload=ctx['result_template']
    h.runner.submit('T1','S1',payload)
    with h.store.unit_of_work() as uow:
        before=uow.tasks.load('T1')
        batch=before.evidence_book.to_dict()['batches'][-1]
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER proof_failure BEFORE INSERT ON task_events WHEN json_extract(NEW.data,'$.event')='evidence_assessed' BEGIN SELECT RAISE(ABORT,'proof failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        h.runner.assess_evidence('T1','S1',batch['tree'],batch['execution_key'])
    with h.store.unit_of_work() as uow:
        after=uow.tasks.load('T1')
        assert after==before and after.evidence_book.to_dict()['arguments']==[]
    assert counter.read_text()=='x'  # The separate observed command receipt is durable.
    with h.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==1
        db.execute('DROP TRIGGER proof_failure')
    assert h.verify()['status']=='verified' and counter.read_text()=='x'


def test_project_automatic_check_stays_guard_when_also_subject(project):
    h,counter=setup(project,kind='check',logical=False,negative=True)
    # No source change needed: command is also an explicit task guard in the plan's role.
    cfg=project['cfg']; cfg['automatic_checks']=[{'paths':['**'],'by_stage':{'measure':['M'],'audit':[]}}]
    write_json(project['config_path'],cfg)
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(project['task_path'])
    data=h.store.current('S1')
    selected=h._select_checks(data,['src/double.py'])
    assert len(selected)==1 and selected[0]['guard'] is True


def test_unknown_method_and_missing_plan_rejected_before_worktree(project):
    for value in [None,{'bad-stage':{'subject_methods':{},'arguments':[],'review_arguments':[]}}]:
        task=dict(project['task'])
        if value is None: del task['evidence_plan']
        else: task['evidence_plan']=value
        write_json(project['task_path'],task)
        h=Poise(project['config_path'],'S1')
        with pytest.raises(PoiseError): h.bootstrap(project['task_path'])
        assert not (project['root']/'state/worktrees/T1').exists()


def test_repeated_pending_does_not_rerun_command(project):
    h,counter=setup(project);ctx=h.bootstrap(project['task_path']); input_result(ctx)
    first=h.verify();assert first['status']=='awaiting_continuation'
    again=h.verify();assert again['status']=='awaiting_continuation'
    assert counter.read_text()=='x' and first['checks']==again['checks']


def test_verification_method_waits_for_process_completion(project):
    h,_=setup(project,logical=False)
    task=project['task'];task['methods'][0].update(
        argv=[sys.executable,'-c',"import time; time.sleep(0.05); print('observed=3')"]
    )
    write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(project['task_path']); input_result(ctx)
    r=h.verify();assert r['status']=='verified' and r['checks'][0]['timed_out'] is False


def test_lost_raw_receipt_blocks_continue_not_silent_reexecution(project):
    h,counter=setup(project);ctx=h.bootstrap(project['task_path']); input_result(ctx);pending=h.verify()
    Path(pending['checks'][0]['stdout']).unlink()
    input_result(pending['context'],[arg([pending['checks'][0]['id']])],phase='continue')
    assert h.verify()['status']=='observations_stale'
    assert counter.read_text()=='x'


def test_cancel_skips_missing_evidence_and_preserves_observation(project):
    h,counter=setup(project);ctx=h.bootstrap(project['task_path']);input_result(ctx);pending=h.verify()
    assert h.cancel('Пользователь отменил')['status']=='cancelled'
    assert Path(pending['checks'][0]['stdout']).is_file() and counter.read_text()=='x'


def test_empty_pre_argument_cannot_be_self_certified(project):
    h,_=setup(project,phase='prepare');ctx=h.bootstrap(project['task_path']);input_result(ctx,[{**arg([]),'facts':[]}])
    with pytest.raises(PoiseError): h.verify()
    assert h.show()['status']=='active'


def test_check_unrecognised_output_is_not_a_product_negative_verdict(project):
    h,_=setup(project,kind='check',logical=False)
    task=project['task'];task['methods'][0]['argv']=[sys.executable,'-c',"print('tool crashed before observation'); raise SystemExit(1)"]
    write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(project['task_path']);input_result(ctx)
    result=h.verify()
    assert result['status']=='checks_failed'
    assert h.show()['status']=='active'


def test_observation_cannot_request_reasoning_on_mutated_target(project):
    h,_=setup(project)
    task=project['task'];task['methods'][0]['argv']=[sys.executable,'-c',"from pathlib import Path; Path('src/generated.py').write_text('x=1'); print('observed=3')"]
    write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(project['task_path']);input_result(ctx)
    with pytest.raises(PoiseError,match='дерево|состояни'):
        h.verify()
    assert h.show()['status']=='active'


def test_push_resume_after_explicit_environment_change_rechecks(project, monkeypatch):
    from conftest import add_test
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(project['task_path']);add_test(ctx['worktree']);fill(ctx)
    offline=project['remote'].with_name('remote-offline.git')
    project['remote'].rename(offline)
    try:
        with pytest.raises(PoiseError): h.verify()
    finally:
        offline.rename(project['remote'])
    assert h.show()['evidence_count']==1
    monkeypatch.setenv('LANG','C')
    report=h.verify()
    assert report['status']=='verified'
    assert h.show()['evidence_count']==2
