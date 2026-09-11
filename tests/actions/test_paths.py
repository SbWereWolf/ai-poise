from copy import deepcopy
from pathlib import Path
import pytest
from conftest import git
from conftest import WorkPoise as Poise
from poise.common import PoiseError
from .helpers import setup,verify,result,advance,call,resolve,inspect,method


def test_merge_conflict_continue_inspect_publish_happy_path(project):
    h,ctx,plan,base=setup(project)
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    out=verify(h,p)
    assert out['status']=='awaiting_action_continuation'
    assert out['action']['conflicts']==['src/double.py']
    assert git(project['remote'],'rev-parse','main')==base
    assert h.show()['status']=='active'
    out=verify(Poise(project['config_path'],'ACTION-AGENT'),resolve(out,ctx['worktree']))
    assert out['status']=='verified' and out['checks'][0]['passed']
    assert git(project['remote'],'rev-parse','main')==base
    assert git(Path(ctx['worktree']),'status','--porcelain')==''
    for src in plan['sources']:
        assert git(Path(ctx['worktree']),'merge-base','--is-ancestor',src['commit'],'HEAD')==''
    final=out['commit']; assert verify(h,None)['replayed']
    ctx2=advance(h);assert ctx2['stage']=='review';inspect(h,ctx2)
    assert git(project['remote'],'rev-parse','main')==base
    ctx3=advance(h); assert ctx3['stage']=='publish'
    pub=result(ctx3,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish reviewed result'})
    final_report=verify(h,pub)
    assert final_report['status']=='verified'
    assert git(project['remote'],'rev-parse','main')==final
    assert verify(h,None)['replayed']
    assert call(h,'accept',{})['status']=='completed'


def test_clean_merge_and_no_source_reexecution_after_failed_check(project):
    h,ctx,plan,base=setup(project,conflict=False)
    out=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert out['status']=='verified' and out['checks'][0]['passed']
    assert len(out['action']['steps'])==2
    assert git(project['app'],'rev-parse','main')==base


def test_conflict_needs_explicit_resolution_and_cannot_change_plan(project):
    h,ctx,plan,base=setup(project)
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]});out=verify(h,p)
    bad=deepcopy(out['context']['result_template']);bad['stage_work']['resolutions']=[]
    with pytest.raises(PoiseError):verify(h,bad)
    bad=deepcopy(p);bad['stage_work']['plan']['sources'].reverse()
    with pytest.raises(PoiseError):verify(h,bad)
    assert git(project['remote'],'rev-parse','main')==base


def test_publish_blocks_target_drift_without_rewriting_remote(project):
    h,ctx,plan,base=setup(project,conflict=False)
    verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}));inspect(h,advance(h));ctx=advance(h)
    app=project['app'];(app/'new.txt').write_text('external target advancement')
    git(app,'add','.');git(app,'commit','-m','new target');git(app,'push','backup','main');other=git(app,'rev-parse','HEAD')
    out=verify(h,result(ctx,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish'}))
    assert out['status']=='action_blocked' and out['action']['reason']=='target_changed'
    assert git(project['remote'],'rev-parse','main')==other
    assert h.show()['status']=='active'


def test_dirty_apply_entry_does_not_overwrite_foreign_work(project):
    h,ctx,plan,base=setup(project)
    file=Path(ctx['worktree'],'src/double.py');file.write_text('unattributed work\n')
    with pytest.raises(PoiseError):verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert file.read_text()=='unattributed work\n'
    assert git(project['remote'],'rev-parse','main')==base


def test_environment_plan_executes_and_probes_batch_once(project):
    h,ctx,_,_=setup(project,kind='commands')
    root=project['root']; target=root/'service.ready';count=root/'applied.txt'
    change=method('INSTALL',f'from pathlib import Path; Path({str(target)!r}).write_text("ready"); p=Path({str(count)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")')
    probe=method('READY',f'from pathlib import Path; assert Path({str(target)!r}).read_text()=="ready"')
    plan={'kind':'commands','steps':[{'id':'install','probe_false_exit_codes':[1],'apply':change,'probe':probe}]}
    out=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert out['status']=='verified' and target.read_text()=='ready' and count.read_text()=='x'
    verify(h,None);assert count.read_text()=='x'
    inspect(h,advance(h));assert call(h,'accept',{})['status']=='completed'


def test_command_plan_stops_after_failure_and_does_not_repeat_effect(project):
    h,ctx,_,_=setup(project,kind='commands')
    mark=project['root']/'mark'
    bad=method('BROKEN',f'from pathlib import Path; Path({str(mark)!r}).write_text("applied"); raise SystemExit(2)')
    probe=method('PROBE','raise SystemExit(1)')
    tail=method('TAIL',f'from pathlib import Path; Path({str(mark)!r}).write_text("WRONG")')
    plan={'kind':'commands','steps':[{'id':'first','probe_false_exit_codes':[1],'apply':bad,'probe':probe},{'id':'second','probe_false_exit_codes':[1],'apply':tail,'probe':probe}]}
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    out=verify(h,p);assert out['status']=='action_failed' and mark.read_text()=='applied'
    assert verify(h,p)['status']=='action_failed' and mark.read_text()=='applied'


def test_failed_combined_test_rechecks_new_tree_without_second_merge(project):
    h,ctx,plan,base=setup(project)
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    out=verify(h,p);p=resolve(out,ctx['worktree'])
    Path(ctx['worktree'],'src/double.py').write_text('def double(n):\n    return n + 7\n')
    failed=verify(h,p);assert failed['status']=='checks_failed'
    with h.store.database.transaction() as db:
        cursor=db.execute('SELECT version FROM action_runs').fetchone()[0]
    Path(ctx['worktree'],'src/double.py').write_text('def double(n):\n    return n * 2\n')
    fixed=verify(Poise(project['config_path'],'ACTION-AGENT'),p)
    assert fixed['status']=='verified' and fixed['checks'][0]['passed']
    with h.store.database.transaction() as db:assert db.execute('SELECT version FROM action_runs').fetchone()[0]==cursor
    assert git(project['remote'],'rev-parse','main')==base


def test_publication_needs_last_artifact_before_target_update(project):
    from conftest import write_json
    # Publish a task whose final requirement is only due at the last stage.
    h,ctx,plan,base=setup(project,conflict=False)
    # Use public task creation with a different ID, not a direct DB patch.
    task=deepcopy(h.current_task()['contract']);call(h,'cancel',{'reason':'replace fixture with fully declared final requirement'})
    task['id']='WITH-ARTIFACT';task['artifact_requirements']=[{'scope':'task','pattern':'approval.txt','minimum':1,'maximum':1}]
    from .helpers import start
    ctx=start(h,task)
    verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}));inspect(h,advance(h));ctx=advance(h)
    p=result(ctx,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish'})
    with pytest.raises(PoiseError):verify(h,p)
    assert git(project['remote'],'rev-parse','main')==base


def test_noop_integration_publishes_no_artificial_commits(project):
    h,ctx,plan,base=setup(project,conflict=False)
    # The second source is already an ancestor of base, so this plan is a no-op.
    plan={'kind':'git_merge','base_commit':base,'sources':[{'commit':base,'checkpoint_message':'No checkpoint needed'}]}
    # Source fixture starts with a broken app; use a separately declared no-test task.
    task=deepcopy(h.current_task()['contract']);call(h,'cancel',{'reason':'no-op fixture'})
    task['id']='NOOP';task['methods']=[];task['method_inputs']=[];task['checks']={s:[] for s in task['checks']}
    from .helpers import start
    ctx=start(h,task)
    out=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert out['status']=='verified' and out['commit']==base
    inspect(h,advance(h));ctx=advance(h)
    out=verify(h,result(ctx,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish no-op'}))
    assert out['status']=='verified' and git(project['remote'],'rev-parse','main')==base


def test_revision_cycle_keeps_findings_and_publishes_only_after_clear_inspection(project):
    from runner.helpers import finding,resolution,decision
    h,ctx,plan,base=setup(project,conflict=False)
    verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    review=inspect(h,advance(h),[finding('F1')]);assert review['stage_outcome']=='changes_requested'
    fix=advance(h);assert fix['stage']=='fix'
    Path(fix['worktree'],'src/double.py').write_text('def double(n):\n    """Preserve both source behaviours."""\n    return n * 2\n')
    verify(h,result(fix,{'resolutions':[resolution('R1','F1')]}))
    follow=advance(h);review=inspect(h,follow,[],[decision('R1','rejected')])
    assert review['stage_outcome']=='changes_requested'
    fix=advance(h);verify(h,result(fix,{'resolutions':[resolution('R2','F1')]}))
    inspect(h,advance(h),[],[decision('R2','accepted')])
    pub=advance(h);assert pub['stage']=='publish'
    assert git(project['remote'],'rev-parse','main')==base
    out=verify(h,result(pub,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User: publish after follow-up'}))
    assert out['status']=='verified'
    assert [d['decision'] for d in out['feedback']['decisions']]==['rejected','accepted']


def test_direct_task_cannot_mark_external_stage_verified_without_receipt(project):
    h,ctx,plan,_=setup(project,conflict=False)
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    receipt=h.runner.submit('INTEGRATE',h.session,p)
    with h.store.unit_of_work() as uow:
        task=uow.tasks.load('INTEGRATE')
        with pytest.raises(PoiseError):task.mark_verified(h.session,receipt.digest,())


def test_resume_known_pending_merge_after_lost_response(project,monkeypatch):
    h,ctx,plan,base=setup(project,conflict=False)
    original=h.plan_actions._git_effect
    crashed=False
    def effect(data,*args):
        nonlocal crashed
        result=original(data,*args)
        if args[0]=='merge' and not crashed:
            crashed=True
            raise RuntimeError('injected crash after merge, before receipt')
        return result
    monkeypatch.setattr(h.plan_actions,'_git_effect',effect)
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    with pytest.raises(RuntimeError):verify(h,p)
    resumed=Poise(project['config_path'],'ACTION-AGENT')
    out=verify(resumed,p)
    assert out['status']=='verified'
    assert git(project['remote'],'rev-parse','main')==base


def test_unknown_command_outcome_uses_probe_not_second_effect(project,monkeypatch):
    h,ctx,_,_=setup(project,kind='commands');target=project['root']/'receipt';count=project['root']/'count'
    apply=method('APPLY',f'from pathlib import Path; p=Path({str(count)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); Path({str(target)!r}).write_text("ok")')
    probe=method('PROBE',f'from pathlib import Path; assert Path({str(target)!r}).read_text()=="ok"')
    original=h.plan_actions._method
    def method_call(data,m):
        r=original(data,m)
        if m['id']=='APPLY':raise RuntimeError('lost action response')
        return r
    monkeypatch.setattr(h.plan_actions,'_method',method_call)
    p=result(ctx,{'plan':{'kind':'commands','steps':[{'id':'S','probe_false_exit_codes':[1],'apply':apply,'probe':probe}]},'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    with pytest.raises(RuntimeError):verify(h,p)
    out=verify(Poise(project['config_path'],h.session),p)
    assert out['status']=='verified' and count.read_text()=='x'


def test_failed_command_can_restart_only_with_explicit_user_rework(project):
    h,ctx,_,_=setup(project,kind='commands')
    plan={'kind':'commands','steps':[{'id':'S','probe_false_exit_codes':[1],'apply':method('A','raise SystemExit(2)'), 'probe':method('P','raise SystemExit(1)')}]}
    p=result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]})
    assert verify(h,p)['status']=='action_failed'
    ctx=call(h,'bootstrap',{'task':None,'decision':'rework','feedback':'User: replace the failed procedure','rework_stage':'apply'})
    assert ctx['iteration']==2
    good={'kind':'commands','steps':[{'id':'S','probe_false_exit_codes':[1],'apply':method('A','print("already ready")'),'probe':method('P','print("ready")')}]}
    assert verify(h,result(ctx,{'plan':good,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))['status']=='verified'


def test_target_push_retry_is_probed_and_bounded_without_repeating_checks(project,monkeypatch):
    h,ctx,plan,base=setup(project,conflict=False)
    verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}));inspect(h,advance(h));ctx=advance(h)
    p=result(ctx,{'target_ref':'refs/heads/main','expected_commit':base,'authorization':'User publish'})
    original=h.plan_actions._git_effect
    def reject(data,*args):
        if args[0]=='push':return {'actual_exit_code':1,'timed_out':False,'rejection':'synthetic transient transport failure'}
        return original(data,*args)
    monkeypatch.setattr(h.plan_actions,'_git_effect',reject)
    assert verify(h,p)['status']=='action_blocked'
    monkeypatch.setattr(h.plan_actions,'_git_effect',original)
    out=verify(h,p)
    assert out['status']=='verified' and git(project['remote'],'rev-parse','main')==out['commit']
    assert out['attempt']==1  # no new check execution for retrying only publication


def test_missing_plan_is_rejected_as_domain_input_not_typeerror(project):
    h,ctx,_,_=setup(project,conflict=False)
    with pytest.raises(PoiseError):verify(h,result(ctx))


def test_unresolved_markers_are_not_committed(project):
    h,ctx,plan,base=setup(project)
    out=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    p=deepcopy(out['context']['result_template'])
    p['stage_work']['resolutions']=[{'path':'src/double.py','reason':'Claimed resolved but markers remain'}]
    with pytest.raises(PoiseError):verify(h,p)
    assert git(project['remote'],'rev-parse','main')==base


def test_only_accepted_inspection_can_lead_to_publish(project):
    from poise.modules.goal_config.domain import GoalTypeDefinition
    h,ctx,_,_=setup(project)
    process=deepcopy(h.current_task()['process'])
    process['stages'][0]['transitions']['complete']='publish'
    with pytest.raises(PoiseError):GoalTypeDefinition.parse(process)


def test_action_receipt_transaction_rolls_back_on_event_error(project):
    import sqlite3
    from poise.modules.actions.domain import PlanSpec
    h,ctx,plan,_=setup(project,conflict=False)
    # The trigger is fault injection in an isolated test store, not an API bypass.
    with h.store.database.transaction() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON action_events BEGIN SELECT RAISE(ABORT,'injected event failure'); END")
    with pytest.raises(sqlite3.IntegrityError):h.plan_actions._obtain(h.current_task(),PlanSpec.parse(plan,10))
    assert h.plan_actions.snapshot(h.current_task()) is None


def test_environment_correction_reuses_apply_plan_and_separate_inspection(project):
    from runner.helpers import finding,resolution,decision
    h,ctx,_,_=setup(project,kind='commands');target=project['root']/'version'
    def plan(value):
        return {'kind':'commands','steps':[{'id':'set-version','probe_false_exit_codes':[1],
          'apply':method('SET',f'from pathlib import Path; Path({str(target)!r}).write_text({value!r})'),
          'probe':method('READ',f'from pathlib import Path; assert Path({str(target)!r}).read_text()=={value!r}')}]}
    verify(h,result(ctx,{'plan':plan('v1'),'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    inspect(h,advance(h),[finding('ENV1')]);fix=advance(h)
    assert fix['handler']=='apply_plan'
    p=result(fix,{'plan':plan('v2'),'phase':'prepare','resolutions':[],'finding_resolutions':[resolution('ER1','ENV1')]})
    out=verify(h,p);assert out['status']=='verified' and target.read_text()=='v2'
    inspected=inspect(h,advance(h),[],[decision('ER1','accepted')])
    assert inspected['feedback']['open_findings']==[]
    assert call(h,'accept',{})['status']=='completed'


def test_unknown_probe_result_blocks_without_applying(project):
    h,ctx,_,_=setup(project,kind='commands');mark=project['root']/'should-not-exist'
    plan={'kind':'commands','steps':[{'id':'S','probe_false_exit_codes':[1],
        'apply':method('APPLY',f'from pathlib import Path; Path({str(mark)!r}).write_text("changed")'),
        'probe':method('PROBE','raise SystemExit(17)')}]}
    out=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert out['status']=='action_blocked' and out['action']['reason']=='probe_not_interpretable'
    assert not mark.exists()
