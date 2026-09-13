from pathlib import Path
from copy import deepcopy
import json
import pytest
from conftest import write_json, git, fill, add_test
from poise.runtime import PoiseError
from conftest import Poise


def test_full_tdd_flow_commits_without_remote_push_and_stops(project):
    h = Poise(project['config_path'], 'SESSION-A')
    b = h.bootstrap(task_file=project['task_path'])
    assert b['stage'] == 'tests'
    assert Path(b['worktree']) != project['app']
    add_test(b['worktree']); fill(b)
    v = h.verify()
    assert v['status'] == 'verified'
    assert v['checks'][0]['actual_exit_code'] == 1
    assert v['checks'][0]['passed'] is True
    assert git(Path(b['worktree']), 'status', '--porcelain') == ''
    assert git(project['remote'], 'for-each-ref', '--format=%(refname)', 'refs/heads/tasks/T1') == ''
    assert h.bootstrap()['stage'] == 'tests'  # no implicit advance
    assert h.verify()['replayed'] is True
    assert not Path(b['runtime_root']).exists()
    assert Path(v['checks'][0]['stderr']).is_file()
    b = h.bootstrap(decision='continue')
    assert b['stage'] == 'test_review'; fill(b)
    assert h.verify()['status'] == 'verified'
    b = h.bootstrap(decision='continue')
    assert b['stage'] == 'implementation'
    Path(b['worktree'], 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    fill(b); v = h.verify()
    assert v['checks'][0]['actual_exit_code'] == 0
    assert v['status'] == 'verified'
    b = h.bootstrap(decision='continue'); fill(b)
    assert h.verify()['status'] == 'verified'
    assert h.accept()['status'] == 'completed'
    assert git(project['app'], 'show', 'main:src/double.py').endswith('return n + 1')


def test_fixed_code_same_payload_runs_new_check(project):
    h = Poise(project['config_path'], 'S-A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b)
    test = Path(b['worktree'], 'tests/test_double.py')
    test.write_text('import this_module_does_not_exist\n')
    first = h.verify(); assert first['status'] == 'checks_failed'
    add_test(b['worktree'])
    second = h.verify(); assert second['status'] == 'verified'
    assert second['attempt'] == 2
    assert h.show()['submission_count'] == 1


def test_missing_section_stops_before_commands(project):
    h = Poise(project['config_path'], 'S-A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree'])
    with pytest.raises(PoiseError, match='report'):
        h.verify()
    assert h.show()['attempts'] == 0


def test_candidate_result_used_for_precheck(project):
    h = Poise(project['config_path'], 'S-A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b, 'Новый результат до precheck.')
    assert h.verify()['status'] == 'verified'


def test_readonly_review_blocks_code_edits(project):
    h = Poise(project['config_path'], 'S-A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b); h.verify()
    b = h.bootstrap(decision='continue'); fill(b)
    Path(b['worktree'], 'src/double.py').write_text('def double(n):\n    return n*2\n')
    with pytest.raises(PoiseError, match='read-only'):
        h.verify()


def test_no_second_task_without_finishing_first(project):
    h = Poise(project['config_path'], 'S-A'); h.bootstrap(task_file=project['task_path'])
    other = dict(project['task'], id='T2')
    p = write_json(project['root'] / 'other.json', other)
    with pytest.raises(PoiseError, match='текущ'):
        h.bootstrap(task_file=p)


def test_parallel_tasks_worktrees_do_not_mix(project):
    a = Poise(project['config_path'], 'A'); b = Poise(project['config_path'], 'B')
    other = dict(project['task'], id='T2')
    p = write_json(project['root'] / 'other.json', other)
    ba = a.bootstrap(task_file=project['task_path']); bb = b.bootstrap(task_file=p)
    add_test(ba['worktree']); fill(ba)
    assert not Path(bb['worktree'], 'tests/test_double.py').exists()
    assert a.verify()['status'] == 'verified'
    assert git(Path(bb['worktree']), 'status', '--porcelain') == ''


def test_cancel_requires_reason_skips_tests(project):
    h = Poise(project['config_path'], 'A'); h.bootstrap(task_file=project['task_path'])
    with pytest.raises(PoiseError): h.cancel('')
    assert h.cancel('Пользователь отменил задачу.')['status'] == 'cancelled'
    assert h.show()['attempts'] == 0


def test_unknown_method_rejected_at_creation(project):
    project['task']['checks']['tests'] = ['K1']
    write_json(project['task_path'], project['task'])
    h = Poise(project['config_path'], 'S-A')
    with pytest.raises(PoiseError, match='K1'):
        h.bootstrap(task_file=project['task_path'])


def test_invalid_trace_schedule_rejected_before_task_branch_or_worktree(project):
    invalid=deepcopy(project['task'])
    invalid['content_contract']={"sections":[],"routes":[
        {"id":"delivery","requirements":invalid['requirements'],"points":[
            {"id":"method","kind":"method","fields":{},"write_stages":["implementation"]}]}],
        "requirements":[
            {"id":"method-too-late","kind":"trace","route":"delivery","point":"method",
             "stages":["implementation"],"phase":"pre","field_equals":{}}]}
    task_path=write_json(project['root']/'invalid-schedule-task.json',invalid)
    h=Poise(project['config_path'],'S-A')
    try:
        h.bootstrap(task_file=task_path)
    except PoiseError as error:
        failure = error
    else:
        pytest.fail("TASK_0119_DIRECT_PRE_GATE_ACCEPTED")
    assert all(value in str(failure) for value in
               ('method-too-late','delivery','method','pre','write_stages','implementation'))
    assert h.task_queries.record('T1') is None
    assert not (project['root']/'state/worktrees/T1').exists()
    assert git(project['app'],'branch','--list','tasks/T1')==''


def test_config_missing_value_not_defaulted(project):
    del project['cfg']['limits']['verify_attempts']
    write_json(project['config_path'], project['cfg'])
    with pytest.raises(PoiseError, match='verify_attempts'):
        Poise(project['config_path'], 'S-A')


def test_accept_does_not_automatically_start_next_stage(project):
    h = Poise(project['config_path'], 'S-A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b); h.verify()
    assert h.accept()['status']=='accepted'
    b = h.bootstrap()
    assert b['stage']=='tests' and b['status']=='accepted' and b['result_template'] is None
    assert h.bootstrap(decision='continue')['stage']=='test_review'


def test_limit_does_not_reset_between_calls(project):
    project['cfg']['limits']['verify_attempts']=1
    write_json(project['config_path'],project['cfg'])
    h = Poise(project['config_path'],'A'); b=h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b)
    Path(b['worktree'],'tests/test_double.py').write_text('import not_existing\n')
    assert h.verify()['status']=='checks_failed'
    h = Poise(project['config_path'],'A')
    with pytest.raises(PoiseError,match='лимит'): h.verify()


def test_expected_red_is_not_missing_interpreter(project):
    project['task']['methods'][0]['argv'][0]='/no/such/interpreter'
    write_json(project['task_path'],project['task'])
    h=Poise(project['config_path'],'A'); b=h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree']); fill(b)
    with pytest.raises(PoiseError,match='Не удалось запустить'): h.verify()
    assert h.show()['status']=='active'


def test_ad_hoc_read_only_requires_no_worktree(project):
    h=Poise(project['config_path'],'A')
    b=h.bootstrap()
    assert b['status']=='read_only'
    assert not (project['root']/'state/worktrees').exists()
    assert h.verify()['status']=='read_only_verified'
    assert not Path(b['runtime_root']).exists()


def test_cli_uses_session_binding_not_task_id(project,monkeypatch):
    import os, subprocess,sys
    env={key:value for key,value in os.environ.items()
         if key not in ('CODEX_SESSION_ID','CODEX_THREAD_ID','POISE_SESSION','POISE_CALLER_BINDING')}
    env.update({'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),
                'POISE_CONFIG':str(project['config_path']),
                'POISE_CALLER_BINDING':str(project['root']/'.cli-caller.json')})
    request={'operation':'bootstrap','input':{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None},'messages':[]}
    boot=subprocess.run([sys.executable,'-m','poise','work'],input=json.dumps(request),env=env,capture_output=True,text=True)
    assert boot.returncode==0,boot.stderr
    b=json.loads(Path(json.loads(boot.stdout)['response_path']).read_text())
    add_test(b['worktree']);fill(b)
    request={'operation':'verify','input':{'result':b['result_template'],'artifacts':[]},'messages':[]}
    run=subprocess.run([sys.executable,'-m','poise','work'],input=json.dumps(request),env=env,capture_output=True,text=True)
    assert run.returncode==0,run.stderr+run.stdout
    out=json.loads(run.stdout)
    assert out['status']=='verified' and len(run.stdout)<=project['cfg']['limits']['output_chars']
    assert Path(out['response_path']).is_file()
