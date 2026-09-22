"""Task/UoW/SQLite and the real git/command happy path with content gates."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
import pytest
from .cli_helpers import INHERITED_IDENTITIES, bootstrap_packet, cli_environment, run_work
from conftest import write_json, fill, add_test, verification_plan
from conftest import Poise
from poise.common import PoiseError
from poise.modules.content_requirements.domain import ContentPolicy


def empty():
    return {"sections":[],"routes":[],"requirements":[]}


def setup(project, goal, extra, *, exit_refs=()):
    project['cfg']['schema']='ddd-accounting-12'
    project['process']['content_contract']=goal
    project['task']['content_contract']=extra
    if exit_refs:
        project['task']['stage_contracts'][0]['exit_requirements'] = list(exit_refs)
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
          {"id":"product-input","kind":"trace","route":"functional","point":"product","stages":["implementation","code_review"],"phase":"pre","field_equals":{}},
          {"id":"method-input","kind":"trace","route":"functional","point":"method","stages":["implementation","code_review"],"phase":"pre","field_equals":{}},
          {"id":"product-now","kind":"trace","route":"functional","point":"product","stages":["tests","test_review","implementation","code_review"],"phase":"post","field_equals":{}},
          {"id":"method-now","kind":"trace","route":"functional","point":"method","stages":["tests","implementation","code_review"],"phase":"post","field_equals":{}},
          {"id":"published-later","kind":"trace","route":"functional","point":"product","stages":["implementation","code_review"],"phase":"post","field_equals":{"state":"documented"}},
          {"id":"verdict-later","kind":"trace","route":"functional","point":"verdict","stages":["code_review"],"phase":"post","field_equals":{"result":"satisfied"}},
          {"id":"reason-later","kind":"trace","route":"reasoning","point":"proof","stages":["code_review"],"phase":"post","field_equals":{}}]}


def test_pre_gate_rechecks_existing_input_before_commands(project):
    from batch.helpers import request
    from runtime_services.test_stage_contract_runtime import (
        _bootstrap, _counts,
    )

    tools, rejected, task = _bootstrap(project, entry_required=True)
    assert rejected['status'] == 'broken'
    assert rejected['failure']['kind'] == 'content_requirements_failed'
    runtime = tools.runtime
    prerequisite = (Path(runtime.state) / runtime.paths['standalone_tasks'] /
                    'T1' / 'artifacts' / 'inputs' / 'ready.txt')
    prerequisite.parent.mkdir(parents=True, exist_ok=True)
    prerequisite.write_text('ready\n', encoding='utf-8')
    runtime.register_artifact_paths([str(prerequisite)], runtime.task_queries.record('T1'))
    context = tools.invoke(request('bootstrap', {
        'task': {'id': task['id']}, 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))
    assert context['status'] == 'active'
    worktree = Path(context['worktree'])
    add_test(worktree)
    payload = fill(context)
    before = _counts(runtime, 'T1')
    before_head = runtime._git(worktree, 'rev-parse', 'HEAD')
    before_diff = runtime._git(worktree, 'status', '--porcelain=v1')
    before_evidence = runtime.show()['evidence_count']
    forbidden = Path(context['task_root']) / 'artifacts' / 'should-not-exist.txt'
    prerequisite.unlink()
    with pytest.raises(PoiseError, match='entry'):
        tools.invoke(request('verify', {
            'result': payload,
            'artifacts': [{'scope': 'task', 'path': 'should-not-exist.txt',
                           'source': {'kind': 'text', 'text': 'no side effect'}}],
        }))
    assert _counts(runtime, 'T1') == before
    assert runtime.show()['evidence_count'] == before_evidence == 0
    assert runtime.show()['attempts'] == 0
    assert not forbidden.exists()
    assert runtime._git(worktree, 'rev-parse', 'HEAD') == before_head
    assert runtime._git(worktree, 'status', '--porcelain=v1') == before_diff
    prerequisite.write_text('ready\n', encoding='utf-8')
    result = tools.invoke(request('verify', {'result': payload, 'artifacts': []}))
    assert result['status'] == 'verified'


def test_declare_fill_and_gate_extra_section_in_one_verify_no_register_call(project):
    h, context = setup(project, empty(), empty())
    frozen = deepcopy(h.stage_contract_context('T1')['current'])
    additions = section_policy('tests')
    add_test(context['worktree'])
    fill(context)
    update(context, content_additions=additions,
           sections={'report': 'Ready', 'rationale': 'Reason for test'})
    assert h.verify()['status'] == 'verified'
    assert h.task_queries.section('T1', 'tests', 'rationale', None)['content'] == 'Reason for test'
    with h.store.transaction() as db:
        before = tuple(tuple(row) for row in db.execute(
            'SELECT version,data FROM content_contracts WHERE task_id=? ORDER BY version',
            ('T1',)))
    submissions = h.show()['submission_count']
    assert h.verify()['replayed'] is True
    assert h.show()['submission_count'] == submissions
    assert h.stage_contract_context('T1')['current'] == frozen
    resumed = h.bootstrap(decision='rework', feedback='Clarify rationale')
    assert resumed['content_requirements']['due'] == []
    assert h.stage_contract_context('T1')['current'] == frozen
    with h.store.unit_of_work() as uow:
        layers = uow.tasks.load('T1').content_policy.to_layers()
    assert layers['goal'] == empty()
    assert layers['task'] == additions
    with h.store.transaction() as db:
        assert tuple(tuple(row) for row in db.execute(
            'SELECT version,data FROM content_contracts WHERE task_id=? ORDER BY version',
            ('T1',))) == before


def test_task_requirement_cannot_replace_goal_minimum_and_invalid_batch_not_saved(project):
    h,b=setup(project,section_policy('tests'),empty()); fill(b)
    changed=section_policy('tests'); changed['requirements'][0]['states']=['empty']
    update(b,content_additions=changed)
    with pytest.raises(PoiseError,match='замен'):
        h.verify()
    assert h.show()['submission_count']==0


def test_full_trace_two_routes_future_document_then_methods_then_review(project):
    # First writes are postconditions; later entry gates consume earlier evidence.
    contracts = project['task']['stage_contracts']
    contracts[0]['exit_requirements'] = ['product-now', 'method-now']
    contracts[1]['exit_requirements'] = ['product-now']
    contracts[2]['entry_requirements'] = ['product-input', 'method-input']
    contracts[2]['exit_requirements'] = ['product-now', 'method-now', 'published-later']
    contracts[3]['entry_requirements'] = ['product-input', 'method-input']
    contracts[3]['exit_requirements'] = [
        'product-now', 'method-now', 'published-later', 'verdict-later', 'reason-later',
    ]
    h, b = setup(project, empty(), trace_policy(project))
    add_test(b['worktree'])
    fill(b)
    update(b, trace={'functional': {
        'product': {'state': 'planned', 'reference': 'docs/double.md#R1'},
        'method': 'RED',
    }})
    first = h.verify()
    assert first['status'] == 'verified'
    first_submission = h.task_queries.section('T1', 'tests', 'report', None)['submission']
    b = h.bootstrap(decision='continue')
    fill(b)
    assert h.verify()['status'] == 'verified'
    b = h.bootstrap(decision='continue')
    implementation = next(
        item for item in h.stage_contract_context('T1')['current']
        if item['stage_id'] == 'implementation'
    )
    assert implementation['entry_requirements'] == [
        'product-input', 'method-input',
    ]
    fill(b)
    Path(b['worktree'], 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    update(b, trace={'functional': {
        'product': {'state': 'documented', 'reference': 'docs/double.md#R1'},
        'method': 'GREEN',
    }})
    # The reference is structural: no claim that this document physically exists.
    assert h.verify()['status'] == 'verified'
    b = h.bootstrap(decision='continue')
    fill(b)
    failed = h.verify()
    assert failed['status'] == 'content_requirements_failed'
    assert failed['content_gate']['phase'] == 'post'
    with pytest.raises(PoiseError):
        h.accept()
    update(b, trace={
        'functional': {'verdict': {
            'result': 'satisfied', 'basis': 'GREEN receipt and inspected code',
        }},
        'reasoning': {'proof': {
            'facts': 'Multiplication verified', 'assumptions': 'Integer n',
            'inference': 'n*2 doubles n', 'conclusion': 'R1 fulfilled',
        }},
    })
    assert h.verify()['status'] == 'verified'
    assert h.accept()['status'] == 'completed'
    h2 = Poise(project['config_path'], 'S1')
    current = h2.task_queries.content('T1')
    assert current['trace']['functional'] == {
        'product': {'state': 'documented', 'reference': 'docs/double.md#R1'},
        'method': 'GREEN',
        'verdict': {'result': 'satisfied', 'basis': 'GREEN receipt and inspected code'},
    }
    assert current['trace']['reasoning']['proof']['conclusion'] == 'R1 fulfilled'
    assert h2.task_queries.trace_point('T1', 'functional', 'product', first_submission)['value'] == {
        'state': 'planned', 'reference': 'docs/double.md#R1',
    }


def test_post_gate_preserves_candidate_checks_but_cannot_mark_verified(project):
    h, b = setup(project, section_policy('tests', 'post'), empty(),
                 exit_refs=('rationale-required',))
    add_test(b['worktree'])
    fill(b)
    worktree = Path(b['worktree'])
    before = h._git(worktree, 'rev-parse', 'HEAD')
    result = h.verify()
    assert result['status'] == 'content_requirements_failed'
    assert result['content_gate']['phase'] == 'post'
    assert h.show()['evidence_count'] == 1
    candidate = h._git(worktree, 'rev-parse', 'HEAD')
    assert candidate != before
    assert h._git(worktree, 'rev-parse', 'HEAD^') == before
    check = result['checks'][0]
    assert check['passed'] and check['capture_complete'] and check['source_unchanged']
    assert check['commit'] == candidate
    assert check['tree'] == h._git(worktree, 'rev-parse', 'HEAD^{tree}')
    record = h.task_queries.record('T1')
    assert record['status'] != 'verified' and record['status'] != 'completed'
    with pytest.raises(PoiseError):
        h.accept()
    assert h.task_queries.record('T1') == record
    update(b, sections={'report': 'Ready', 'rationale': 'Checked outcome'})
    assert h.verify()['status'] == 'verified'
    assert h.task_queries.section('T1', 'tests', 'rationale', None)['content'] == 'Checked outcome'


def test_artifact_cardinality_by_stage_uses_existing_path_only_interface(project):
    policy = empty()
    policy['requirements'] = [{
        'id': 'two-fixtures', 'kind': 'artifact', 'scope': 'task',
        'pattern': 'fixtures/*.json', 'minimum': 2, 'maximum': 2,
        'stages': ['tests'], 'phase': 'post',
        'source': {'kind': 'stage_output', 'producer_stage': 'tests'},
    }]
    h, b = setup(project, policy, empty(), exit_refs=('two-fixtures',))
    add_test(b['worktree'])
    a = Path(b['task_root']) / 'artifacts' / 'fixtures' / 'a.json'
    a.parent.mkdir(parents=True)
    a.write_text('{}', encoding='utf-8')
    missing = a.with_name('missing.json')
    fill(b, artifacts=[str(a), str(missing)])
    before = h.show()['submission_count']
    with pytest.raises(PoiseError, match='Артефакт не существует'):
        h.verify()
    assert h.show()['submission_count'] == before
    assert not missing.exists()

    # Duplicate path strings still denote one existing artifact, not two.
    fill(b, artifacts=[str(a), str(a)])
    duplicate = h.verify()
    assert duplicate['status'] == 'content_requirements_failed'
    assert duplicate['content_gate']['phase'] == 'post'
    with pytest.raises(PoiseError):
        h.accept()
    c = a.with_name('b.json')
    c.write_text('{}', encoding='utf-8')
    fill(b, artifacts=[str(a), str(c)])
    result = h.verify()
    assert result['status'] == 'verified'
    assert len(result['artifacts']) == 2
    assert {item['path'] for item in result['artifacts']} == {str(a), str(c)}
    assert a.read_text() == c.read_text() == '{}'


def test_missing_required_content_does_not_block_user_cancel(project):
    h, b = setup(project, section_policy('tests', 'post'), empty(),
                 exit_refs=('rationale-required',))
    add_test(b['worktree'])
    fill(b)
    result = h.verify()
    assert result['status'] == 'content_requirements_failed'
    assert result['content_gate']['phase'] == 'post'
    evidence_before = h.show()['evidence_count']
    assert evidence_before == 1
    assert h.cancel('User stopped work')['status'] == 'cancelled'
    assert h.task_queries.record('T1')['status'] == 'cancelled'
    assert h.store.counts('T1')[1] == evidence_before


def test_new_content_and_trace_are_atomic_with_submission(project):
    h,b=setup(project,empty(),empty()); add_test(b['worktree']); fill(b)
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
    h, _ = setup(project, empty(), empty())
    source = h._task()
    contract = deepcopy(source['contract'])
    contract['id'] = 'LEGACY'
    legacy = {'sections': [], 'routes': [{
        'id': 'delivery', 'requirements': contract['requirements'],
        'points': [{'id': 'method', 'kind': 'method', 'fields': {},
                    'write_stages': ['tests']}],
    }], 'requirements': [{
        'id': 'method-too-late', 'kind': 'trace', 'route': 'delivery',
        'point': 'method', 'stages': ['implementation'], 'phase': 'pre',
        'field_equals': {},
    }]}
    contract['content_contract'] = legacy
    arguments = (
        source['process']['content_contract'], legacy,
        tuple(stage['id'] for stage in source['process']['stages']),
        tuple(contract['requirements']),
        tuple(method['id'] for method in contract['methods']),
        tuple(sorted({name for stage in source['process']['stages']
                      for name in stage['sections']})),
    )
    # Only rehydration tolerates the historical schedule; new creation still rejects it.
    with pytest.raises(PoiseError, match='method-too-late'):
        ContentPolicy.from_layers(*arguments)
    policy = ContentPolicy.restore_layers(*arguments)
    with h.store.transaction() as db:
        original_row = tuple(db.execute('SELECT * FROM tasks WHERE id=?', ('T1',)).fetchone())
    with h.store.unit_of_work() as uow:
        original = uow.tasks.load('T1')
        assert original.state.claimed_by is not None
        historical = replace(
            original,
            state=replace(original.state, task_id='LEGACY', claimed_by=None),
            content_policy=policy,
        )
        uow.tasks.create(historical, {
            'contract': contract, 'process': deepcopy(source['process']),
            'sprint_id': None, 'goal': contract['goal'],
            'config_hash': source['config_hash'],
        })
    with h.store.transaction() as db:
        before = tuple(tuple(row) for row in db.execute(
            'SELECT version,data FROM content_contracts WHERE task_id=? ORDER BY version',
            ('LEGACY',)))
        legacy_row = tuple(db.execute('SELECT * FROM tasks WHERE id=?', ('LEGACY',)).fetchone())
    with h.store.unit_of_work() as uow:
        restored = uow.tasks.load('LEGACY')
    assert restored.content_policy.to_layers()['task'] == legacy
    assert restored.state.version == historical.state.version
    assert restored.state.claimed_by is None
    with h.store.transaction() as db:
        assert tuple(tuple(row) for row in db.execute(
            'SELECT version,data FROM content_contracts WHERE task_id=? ORDER BY version',
            ('LEGACY',))) == before
        assert tuple(db.execute('SELECT * FROM tasks WHERE id=?', ('LEGACY',)).fetchone()) == legacy_row
        assert tuple(db.execute('SELECT * FROM tasks WHERE id=?', ('T1',)).fetchone()) == original_row
        assert [tuple(row) for row in db.execute(
            'SELECT id,claimed_by FROM tasks WHERE claimed_by IS NOT NULL ORDER BY id'
        )] == [('T1', original.state.claimed_by)]
        assert [row[0] for row in db.execute('PRAGMA integrity_check')] == ['ok']
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_domain_mark_verified_cannot_bypass_content_gates(project):
    h, b = setup(project, section_policy('tests', 'post'), empty(),
                 exit_refs=('rationale-required',))
    add_test(b['worktree'])
    fill(b)
    assert h.verify()['status'] == 'content_requirements_failed'
    with h.store.unit_of_work() as uow:
        task = uow.tasks.load('T1')
    assert task.stage_contracts.stage('tests').exit_requirements == ('rationale-required',)
    with pytest.raises(PoiseError, match='содержим'):
        task.mark_verified(h.session, task.state.submission_digest, ())


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
    route={'id':'new-test','requirements':project['task']['requirements'],'points':[
        {'id':'method','kind':'method','fields':{},'write_stages':['tests']}]}
    additions={'sections':[],'routes':[route],'requirements':[
        {'id':'method-required','kind':'trace','route':'new-test','point':'method','stages':['tests'],'phase':'post','field_equals':{}}]}
    # Declare the post gate before freezing the Task; register method and trace together.
    h,b=setup(project,empty(),additions,exit_refs=('method-required',)); fill(b)
    method={'id':'ADDED','argv':[sys.executable,'-c',"print('executed-new-method')"],'cwd':'.','environment':{},
            'source_under_test':{'kind':'external','reason':'The registration probe reads no repository source.'},
            'verification_plan':verification_plan(
                'Verify the newly registered external observation.',[],green_stages=['tests','code_review']),
            'expected_exit_code':0,'stdout_contains':['executed-new-method'],'stderr_contains':[]}
    update(b,trace={'new-test':{'method':'ADDED'}},
           method_additions=[{'method':method,'stages':['tests','code_review']}])
    result=h.verify()
    assert result['status']=='verified'
    assert [c['method'] for c in result['checks']]==['ADDED']
    assert h.task_queries.content('T1')['trace']['new-test']['method']=='ADDED'
    before=h.show()['submission_count']
    assert h.verify()['replayed']
    assert h.show()['submission_count']==before
    current=h._task()
    assert current['contract']['checks']['code_review']==['ADDED']
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_methods WHERE task_id='T1' AND method_id='ADDED'").fetchone()[0]==1


@pytest.mark.parametrize("inherited", INHERITED_IDENTITIES)
def test_cli_content_failure_is_business_failure_not_exit_zero(project, tmp_path, monkeypatch, inherited):
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    binding = tmp_path / "content-caller.json"
    environment = cli_environment(project, binding)
    first, identity = run_work(environment, bootstrap_packet())
    assert first.returncode == 0, first.stdout + first.stderr
    assert identity["status"] == "read_only"
    caller_bytes = binding.read_bytes()
    task = deepcopy(project["task"])
    task["content_contract"] = section_policy("tests")
    task["stage_contracts"][0]["entry_requirements"] = ["rationale-required"]
    # An unmet first-stage entry contract must fail before claim/context, not
    # after constructing an impossible active Task solely to call verify.
    result, failure = run_work(environment, bootstrap_packet(task))
    assert result.returncode == 1, result.stdout + result.stderr
    assert failure["status"] == "broken"
    assert failure["failure"]["kind"] == "content_requirements_failed"
    assert failure["failure"]["content_requirements"]["phase"] == "pre"
    assert binding.read_bytes() == caller_bytes
    state = project["root"] / project["cfg"]["paths"]["state"]
    database = state / project["cfg"]["paths"]["database"]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT status,claimed_by FROM tasks WHERE id='T1'").fetchone() == ("available", None)
        for table in ("submissions", "evidence", "action_runs", "task_results"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id='T1'").fetchone()[0] == 0
    assert not (state / project["cfg"]["paths"]["worktrees"] / "T1").exists()
    final, resumed = run_work(environment, bootstrap_packet())
    assert final.returncode == 0, final.stdout + final.stderr
    assert resumed["status"] == "read_only" and resumed["task"] is None
    assert resumed["session"] == identity["session"]



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
    from batch.helpers import request
    from conftest import WorkPoise
    from runtime_services.test_stage_contract_runtime import (
        _bootstrap, _full_contract_snapshot, _transition,
    )

    # These are distinct synthetic fixture actors, not independent engineering review.
    tools, context, task = _bootstrap(project, session='P05-REVIEWER', inspection=True)
    runtime = tools.runtime
    additions = section_policy('test_review', 'post')
    payload = fill(context)
    payload['content_additions'] = additions
    payload['stage_work']['coverage'] = 'Fixture inspects additive content before explicit activation.'
    runtime.task_commands.submit('T1', runtime.session, payload)
    frozen = _full_contract_snapshot(runtime)
    assert frozen['contracts'] == task['stage_contracts']
    assert runtime.task_commands.assess_content('T1', 'post', ()).to_dict()['requirements'] == []
    revised = deepcopy(task['stage_contracts'][0])
    revised['exit_requirements'] = ['rationale-required']
    version = runtime.task_queries.record('T1')['version']
    foreign = WorkPoise(project['config_path'], 'P05-EXECUTOR')
    for actor, expected, role, identity, message in (
        (foreign, version, 'reviewer', 'foreign', 'owned'),
        (runtime, version, 'executor', 'wrong-role', 'authorization'),
        (runtime, version + 1, 'reviewer', 'stale', 'version'),
    ):
        with pytest.raises(PoiseError, match=message):
            _transition(actor, 'revise', request_id=identity, expected_version=expected,
                        stage_id='test_review', contract=revised, role=role)
        assert _full_contract_snapshot(runtime) == frozen
    first = _transition(runtime, 'revise', request_id='activate-rationale',
                        expected_version=version, stage_id='test_review',
                        contract=revised, role='reviewer')
    after = _full_contract_snapshot(runtime)
    assert after['contracts'][0] == revised
    replay = _transition(runtime, 'revise', request_id='activate-rationale',
                         expected_version=version, stage_id='test_review',
                         contract=revised, role='reviewer')
    assert replay == {**first, 'replayed': True}
    assert _full_contract_snapshot(runtime) == after
    context = tools.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert [item['id'] for item in context['content_requirements']['due']] == ['rationale-required']
    payload = fill(context)
    payload['stage_work']['coverage'] = 'Fixture checks the explicitly activated rationale.'
    result = tools.invoke(request('verify', {'result': payload, 'artifacts': []}))
    assert result['status'] == 'content_requirements_failed'
    assert result['content_gate']['phase'] == 'post'
    before_accept = _full_contract_snapshot(runtime)
    with pytest.raises(PoiseError):
        runtime.accept()
    assert _full_contract_snapshot(runtime) == before_accept
    payload['sections']['rationale'] = 'The explicitly activated requirement is fulfilled.'
    assert tools.invoke(request('verify', {'result': payload, 'artifacts': []}))['status'] == 'verified'
    with runtime.store.unit_of_work() as uow:
        layers = uow.tasks.load('T1').content_policy.to_layers()
    assert layers['task'] == additions


def test_content_demo_cli_route_with_feedback(tmp_path):
    import os, subprocess, sys
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(root/'examples/content_demo.py'),'--directory',str(tmp_path/'content-demo')],
        env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=60)
    assert result.returncode==0, result.stdout+result.stderr
    report=json.loads((tmp_path/'content-demo/content-demo-report.json').read_text())
    assert report['task_status']=='completed' and report['base_unchanged'] and report['doc_in_task_worktree']
    assert sum(c['status']=='content_requirements_failed' for c in report['calls'])==2
    assert report['status'] == 'PASS' and report['base_clean']
    assert report['doc_absent_from_base']
    assert report['content_failures'] == [
        {'stage': 'tests', 'phase': 'post', 'requirements': ['goal-notes']},
        {'stage': 'code_review', 'phase': 'post', 'requirements': ['reason-later']},
    ]
    assert report['rework']['stage'] == 'code_review'
    assert report['rework']['to_iteration'] == report['rework']['from_iteration'] + 1
    assert report['rework']['requested_feedback'] == 'Clarify the proof, preserving previous result.'
    assert report['trace']['functional']['product']['state'] == 'documented'
    assert report['trace']['functional']['verdict']['result'] == 'satisfied'
    assert report['trace']['reasoning']['proof']['inference'] == 'For every integer n, n+n equals n*2.'
    assert report['caller_binding_unchanged']
