"""Task/UoW/SQLite and the real git/command happy path with content gates."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
import pytest
from conftest import write_json, fill, add_test, verification_plan
from conftest import Poise
from poise.common import PoiseError
from poise.modules.content_requirements.domain import ContentPolicy


def empty():
    return {"sections":[],"routes":[],"requirements":[]}


def setup(project, goal, extra):
    project['cfg']['schema']='ddd-accounting-11'
    project['process']['content_contract']=goal
    project['task']['content_contract']=extra
    write_json(project['config_path'],project['cfg'])
    write_json(project['root']/'config/processes/development.json',project['process'])
    write_json(project['task_path'],project['task'])
    h=Poise(project['config_path'],'S1')
    return h,h.bootstrap(task_file=project['task_path'])


def update(context, **changes):
    context['result_template'].update(changes)


def section_policy(stage, phase='pre'):
    return {"sections":[{"id":"rationale","template":"TODO rationale","normalization":"strip","write_stages":[stage]}],
            "routes":[],"requirements":[{"id":"rationale-required","kind":"section","section":"rationale","stages":[stage],"phase":phase,"states":["populated"]}]}


def trace_policy(project):
    req=project['task']['requirements'][0]
    return {"sections":[],"routes":[
        {"id":"functional","requirements":[req],"points":[
            {"id":"product","kind":"record","fields":{"state":["planned","documented"],"reference":[]},"write_stages":["tests","implementation"]},
            {"id":"method","kind":"method","fields":{},"write_stages":["tests","implementation"]},
            {"id":"verdict","kind":"record","fields":{"result":["satisfied","not_satisfied"],"basis":[]},"write_stages":["code_review"]}]},
        {"id":"reasoning","requirements":[req],"points":[
            {"id":"proof","kind":"record","fields":{"facts":[],"assumptions":[],"inference":[],"conclusion":[]},"write_stages":["code_review"]}]}],
        "requirements":[
          {"id":"product-now","kind":"trace","route":"functional","point":"product","stages":["tests","test_review","implementation","code_review"],"phase":"pre","field_equals":{}},
          {"id":"method-now","kind":"trace","route":"functional","point":"method","stages":["tests","implementation","code_review"],"phase":"pre","field_equals":{}},
          {"id":"published-later","kind":"trace","route":"functional","point":"product","stages":["implementation","code_review"],"phase":"pre","field_equals":{"state":"documented"}},
          {"id":"verdict-later","kind":"trace","route":"functional","point":"verdict","stages":["code_review"],"phase":"pre","field_equals":{"result":"satisfied"}},
          {"id":"reason-later","kind":"trace","route":"reasoning","point":"proof","stages":["code_review"],"phase":"pre","field_equals":{}}]}


def test_pre_gate_uses_new_candidate_and_blocks_commands_until_section_filled(project):
    h,b=setup(project,section_policy('tests'),empty()); add_test(b['worktree']); fill(b)
    before=h._git(Path(b['worktree']),'rev-parse','HEAD')
    failed=h.verify()
    assert failed['status']=='content_requirements_failed'
    assert failed['content_gate']['phase']=='pre'
    assert h.show()['evidence_count']==0 and h.show()['attempts']==0
    assert h._git(Path(b['worktree']),'rev-parse','HEAD')==before
    assert h.show()['submission_count']==1
    update(b,sections={'report':'Tests written','rationale':'Regression demonstrates the defect'})
    assert h.verify()['status']=='verified'
    assert h.task_queries.section('T1','tests','rationale',None)['content']=='Regression demonstrates the defect'
    assert not Path(b['runtime_root']).exists()


def test_declare_fill_and_gate_extra_section_in_one_verify_no_register_call(project):
    h,b=setup(project,empty(),empty()); add_test(b['worktree']); fill(b)
    update(b,content_additions=section_policy('tests'),sections={'report':'Ready','rationale':'Reason for test'})
    result=h.verify(); assert result['status']=='verified'
    first=h.show()['submission_count']; assert h.verify()['replayed']
    assert h.show()['submission_count']==first
    resumed=h.bootstrap(decision='rework',feedback='Clarify rationale')
    assert any(r['id']=='rationale-required' for r in resumed['content_requirements']['due'])
    fill(resumed); update(resumed,sections={'report':'Revised','rationale':'Improved reasoning'})
    assert h.verify()['status']=='verified'
    with h.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM content_contracts WHERE task_id=?',('T1',)).fetchone()[0]==2


def test_task_requirement_cannot_replace_goal_minimum_and_invalid_batch_not_saved(project):
    h,b=setup(project,section_policy('tests'),empty()); fill(b)
    changed=section_policy('tests'); changed['requirements'][0]['states']=['empty']
    update(b,content_additions=changed)
    with pytest.raises(PoiseError,match='замен'):
        h.verify()
    assert h.show()['submission_count']==0


def test_full_trace_two_routes_future_document_then_methods_then_review(project):
    h,b=setup(project,empty(),trace_policy(project)); add_test(b['worktree']); fill(b)
    update(b,trace={'functional':{'product':{'state':'planned','reference':'docs/double.md#R1'},'method':'RED'}})
    first=h.verify(); assert first['status']=='verified'
    first_submission=h.task_queries.section('T1','tests','report',None)['submission']
    b=h.bootstrap(decision='continue'); fill(b); assert h.verify()['status']=='verified'
    b=h.bootstrap(decision='continue'); fill(b)
    Path(b['worktree'],'src/double.py').write_text('def double(n):\n    return n * 2\n')
    update(b,trace={'functional':{'product':{'state':'documented','reference':'docs/double.md#R1'},'method':'GREEN'}})
    # Structural reference only; no invented file-existence proof.
    assert h.verify()['status']=='verified'
    b=h.bootstrap(decision='continue'); fill(b)
    assert h.verify()['status']=='content_requirements_failed'
    update(b,trace={'functional':{'verdict':{'result':'satisfied','basis':'GREEN receipt and inspected code'}},
                    'reasoning':{'proof':{'facts':'Multiplication verified','assumptions':'Integer n','inference':'n*2 doubles n','conclusion':'R1 fulfilled'}}})
    assert h.verify()['status']=='verified'
    assert h.accept()['status']=='completed'
    h2=Poise(project['config_path'],'S1')
    current=h2.task_queries.content('T1')
    assert current['trace']['functional']['method']=='GREEN'
    assert current['trace']['reasoning']['proof']['conclusion']=='R1 fulfilled'
    assert h2.task_queries.trace_point('T1','functional','product',first_submission)['value']['state']=='planned'


def test_post_gate_runs_after_checks_but_cannot_mark_verified_or_commit(project):
    h,b=setup(project,section_policy('tests','post'),empty()); add_test(b['worktree']); fill(b)
    before=h._git(Path(b['worktree']),'rev-parse','HEAD')
    result=h.verify()
    assert result['status']=='content_requirements_failed'
    assert result['content_gate']['phase']=='post'
    assert h.show()['evidence_count']==1
    assert h._git(Path(b['worktree']),'rev-parse','HEAD')==before
    update(b,sections={'report':'Ready','rationale':'Checked outcome'})
    assert h.verify()['status']=='verified'


def test_artifact_cardinality_by_stage_uses_existing_path_only_interface(project):
    policy=empty(); policy['requirements']=[{'id':'two-fixtures','kind':'artifact','scope':'task','pattern':'fixtures/*.json','minimum':2,'maximum':2,'stages':['tests'],'phase':'pre'}]
    h,b=setup(project,policy,empty()); add_test(b['worktree'])
    a=Path(b['task_root'],'fixtures/a.json'); a.parent.mkdir(parents=True); a.write_text('{}')
    fill(b,artifacts=[str(a),str(a)])
    assert h.verify()['status']=='content_requirements_failed'
    c=a.with_name('b.json'); c.write_text('{}')
    fill(b,artifacts=[str(a),str(c)])
    result=h.verify(); assert result['status']=='verified' and len(result['artifacts'])==2


def test_missing_required_content_does_not_block_user_cancel(project):
    h,b=setup(project,section_policy('tests'),empty()); fill(b)
    assert h.verify()['status']=='content_requirements_failed'
    assert h.cancel('User stopped work')['status']=='cancelled'
    assert h.show()['evidence_count']==0


def test_new_content_and_trace_are_atomic_with_submission(project):
    h,b=setup(project,empty(),empty()); fill(b)
    update(b,content_additions=trace_policy(project),trace={'functional':{'method':'RED'}})
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_step02 BEFORE INSERT ON task_events BEGIN SELECT RAISE(ABORT,'test-content-fault'); END")
    with pytest.raises(sqlite3.IntegrityError,match='test-content-fault'):
        h.verify()
    with h.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM content_contracts').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM trace_point_layers').fetchone()[0]==0
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_invalid_trace_schedule_in_active_additions_is_rejected_before_submission(project):
    h,b=setup(project,empty(),empty()); add_test(b['worktree']); fill(b)
    route={"id":"late","requirements":project['task']['requirements'],"points":[
        {"id":"method","kind":"method","fields":{},"write_stages":["tests"]}]}
    additions={"sections":[],"routes":[route],"requirements":[
        {"id":"late-method","kind":"trace","route":"late","point":"method",
         "stages":["implementation"],"phase":"pre","field_equals":{}}]}
    update(b,content_additions=additions)
    before=h.task_queries.record('T1')['_version']
    with pytest.raises(PoiseError) as error:
        h.verify()
    assert all(value in str(error.value) for value in
               ('late-method','late','method','tests','implementation'))
    assert h.task_queries.record('T1')['_version']==before
    assert h.show()['submission_count']==0
    with h.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM content_contracts WHERE task_id=?',('T1',)).fetchone()[0]==1


def test_repository_rehydrates_historical_invalid_schedule_without_rewriting_it(project):
    h,_=setup(project,empty(),empty())
    source=h._task();contract=deepcopy(source['contract']);contract['id']='LEGACY'
    legacy={"sections":[],"routes":[
        {"id":"delivery","requirements":contract['requirements'],"points":[
            {"id":"method","kind":"method","fields":{},"write_stages":["tests"]}]}],
        "requirements":[
            {"id":"method-too-late","kind":"trace","route":"delivery","point":"method",
             "stages":["implementation"],"phase":"pre","field_equals":{}}]}
    contract['content_contract']=legacy
    stages=tuple(s['id'] for s in source['process']['stages'])
    policy=ContentPolicy.restore_layers(source['process']['content_contract'],legacy,stages,
        tuple(contract['requirements']),tuple(m['id'] for m in contract['methods']),
        tuple(sorted({name for stage in source['process']['stages'] for name in stage['sections']})))
    with h.store.unit_of_work() as uow:
        original=uow.tasks.load('T1')
        historical=replace(original,state=replace(original.state,task_id='LEGACY'),content_policy=policy)
        uow.tasks.create(historical,{'contract':contract,'process':deepcopy(source['process']),
            'sprint_id':None,'goal':contract['goal'],'config_hash':source['config_hash']})
    with h.store.unit_of_work() as uow:
        restored=uow.tasks.load('LEGACY')
    assert restored.content_policy.to_layers()['task']==legacy
    assert restored.state.version==historical.state.version


def test_domain_mark_verified_cannot_bypass_content_gates(project):
    h,b=setup(project,section_policy('tests'),empty()); fill(b)
    assert h.verify()['status']=='content_requirements_failed'
    with h.store.unit_of_work() as uow:
        task=uow.tasks.load('T1')
    with pytest.raises(PoiseError,match='содержим'):
        task.mark_verified('S1',task.state.submission_digest,())


def test_old_version_two_store_is_rejected_without_migration(project):
    from poise.infrastructure.sqlite.database import Database
    p=project['root']/'old.sqlite'
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE precious(value TEXT)')
        db.execute("INSERT INTO precious VALUES('untouched')")
        db.execute('PRAGMA user_version=2')
    before=p.read_bytes()
    with pytest.raises(PoiseError,match='миграц'):
        Database(p,project['root']/'old.lock',2,0.01)
    assert p.read_bytes()==before


def test_method_can_be_registered_with_trace_in_same_stage_result_and_is_executed(project):
    import sys
    project['task']['methods']=[]
    project['task']['method_inputs']=[]
    project['task']['checks']={s['id']:[] for s in project['process']['stages']}
    project['cfg']['automatic_checks']=[]
    h,b=setup(project,empty(),empty()); fill(b)
    route={'id':'new-test','requirements':project['task']['requirements'],'points':[
        {'id':'method','kind':'method','fields':{},'write_stages':['tests']}]}
    additions={'sections':[],'routes':[route],'requirements':[
        {'id':'method-required','kind':'trace','route':'new-test','point':'method','stages':['tests'],'phase':'pre','field_equals':{}}]}
    method={'id':'ADDED','argv':[sys.executable,'-c',"print('executed-new-method')"],'cwd':'.','environment':{},
            'source_under_test':{'kind':'external','reason':'The registration probe reads no repository source.'},
            'verification_plan':verification_plan(
                'Verify the newly registered external observation.',[],green_stages=['tests','code_review']),
            'expected_exit_code':0,'stdout_contains':['executed-new-method'],'stderr_contains':[]}
    update(b,content_additions=additions,trace={'new-test':{'method':'ADDED'}},
           method_additions=[{'method':method,'stages':['tests','code_review']}])
    result=h.verify()
    assert result['status']=='verified'
    assert [c['method'] for c in result['checks']]==['ADDED']
    assert h.verify()['replayed']
    current=h._task()
    assert current['contract']['checks']['code_review']==['ADDED']
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_methods WHERE task_id='T1' AND method_id='ADDED'").fetchone()[0]==1


def test_cli_content_failure_is_business_failure_not_exit_zero(project):
    import os, subprocess, sys
    h,b=setup(project,section_policy('tests'),empty()); fill(b)
    result=subprocess.run([sys.executable,'-m','poise','work'],input=json.dumps({'operation':'verify','input':{'result':b['result_template'],'artifacts':[]},'messages':[]}),capture_output=True,text=True,
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
             'POISE_CONFIG':str(project['config_path']),'POISE_SESSION':'S1'},timeout=15)
    assert result.returncode==1, result.stdout+result.stderr
    assert 'content_requirements_failed' in result.stdout
    assert h.show()['evidence_count']==0


def test_method_registration_rolls_back_with_failed_content_batch(project):
    import sys
    h,b=setup(project,empty(),empty()); fill(b)
    method={'id':'NEW','argv':[sys.executable,'-c','print(1)'],'cwd':'.','environment':{},
            'source_under_test':{'kind':'external','reason':'The rollback probe reads no repository source.'},
            'verification_plan':verification_plan(
                'Verify the rollback observation.',[],green_stages=['tests']),
            'expected_exit_code':0,'stdout_contains':['1'],'stderr_contains':[]}
    update(b,method_additions=[{'method':method,'stages':['tests']}])
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_all BEFORE INSERT ON task_events BEGIN SELECT RAISE(ABORT,'rollback-all'); END")
    with pytest.raises(sqlite3.IntegrityError,match='rollback-all'): h.verify()
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_methods WHERE method_id='NEW'").fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==0


def test_invalid_method_addition_plan_is_rejected_before_any_owner_mutation(project):
    import sys

    h,b=setup(project,empty(),empty());fill(b)
    method={
        'id':'DOCS_TOO_EARLY',
        'argv':[sys.executable,'-B','-c',"print('docs-green')"],
        'cwd':'.',
        'environment':{},
        'source_under_test':{
            'kind':'external',
            'reason':'The deterministic contract command reads no repository source.',
        },
        'verification_plan':verification_plan(
            'Verify documentation that this process cannot produce.',
            ['docs/**'],
            green_stages=['tests'],
        ),
        'expected_exit_code':0,
        'stdout_contains':['docs-green'],
        'stderr_contains':[],
    }
    update(b,method_additions=[{'method':method,'stages':['tests']}])
    before=h.task_queries.record('T1')
    before_files=sorted(str(path.relative_to(Path(b['task_root'])))
                        for path in Path(b['task_root']).rglob('*'))

    with pytest.raises(PoiseError) as caught:
        h.verify()

    message=str(caught.value)
    assert all(token in message for token in
               ('DOCS_TOO_EARLY','tests','docs/**','allowed_paths'))
    assert h.task_queries.record('T1')==before
    assert h.show()['submission_count']==0
    assert sorted(str(path.relative_to(Path(b['task_root'])))
                  for path in Path(b['task_root']).rglob('*'))==before_files
    with h.store.transaction() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_methods WHERE task_id='T1' AND method_id='DOCS_TOO_EARLY'"
        ).fetchone()[0]==0


def test_accepted_result_cannot_hide_unsatisfied_newly_registered_goal_requirements(project):
    h,b=setup(project,empty(),empty()); fill(b)
    # The new minimum is explicitly saved even though its content is absent.
    update(b,content_additions=section_policy('tests'))
    r=h.verify(); assert r['status']=='content_requirements_failed'
    with pytest.raises(PoiseError): h.accept()
    fresh=h.bootstrap()
    assert fresh['content_requirements']['due'][0]['id']=='rationale-required'


def test_content_demo_cli_route_with_feedback(tmp_path):
    import os, subprocess, sys
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(root/'examples/content_demo.py'),'--directory',str(tmp_path/'content-demo')],
        env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=60)
    assert result.returncode==0, result.stdout+result.stderr
    report=json.loads((tmp_path/'content-demo/content-demo-report.json').read_text())
    assert report['task_status']=='completed' and report['base_unchanged'] and report['doc_in_task_worktree']
    assert sum(c['status']=='content_requirements_failed' for c in report['calls'])==2
