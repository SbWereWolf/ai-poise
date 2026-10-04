import json,os,subprocess,sys
from pathlib import Path
import pytest
from .helpers import configure,request,message


def call(project,packet):
    raw=packet if isinstance(packet,str) else json.dumps(packet)
    inherited={key:value for key,value in os.environ.items()
               if key not in ('CODEX_SESSION_ID','CODEX_THREAD_ID','POISE_SESSION','POISE_CALLER_BINDING')}
    return subprocess.run([sys.executable,'-m','poise','work'],input=raw,text=True,capture_output=True,
        env={**inherited,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
             'POISE_CONFIG':str(project['config_path']),
             'POISE_CALLER_BINDING':str(project['root']/'.cli-caller.json')},timeout=15)


def test_cli_unknown_duplicate_nan_and_size_reject_before_task(project):
    configure(project)
    for raw in ('{"operation":"show","operation":"verify"}', '{"x":NaN}', '{"operation":"missing","input":{},"messages":[]}'):
        r=call(project,raw);assert r.returncode==2
    project['cfg']['batch']['max_input_bytes']=30
    project['config_path'].write_text(json.dumps(project['cfg']))
    r=call(project,' '*31);assert r.returncode==2 and 'max_input_bytes' in r.stdout
    assert not (project['root']/'state/worktrees/T1').exists()


def test_cli_bounded_json_full_receipt_not_truncated_json(project):
    configure(project)
    r=call(project,request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None},[message()]))
    assert r.returncode==0,r.stdout+r.stderr
    view=json.loads(r.stdout);assert len(r.stdout)<=project['cfg']['limits']['output_chars']
    full=json.loads(Path(view['response_path']).read_text())
    assert full['result_template']['sections'] and 'result_path' not in full
    assert full['interaction']['observed_messages_count']==1


def test_cli_no_file_input_compatibility(project):
    configure(project)
    old=subprocess.run([sys.executable,'-m','poise','verify'],input='{}',text=True,capture_output=True,timeout=15)
    assert old.returncode==2


def test_cli_accepts_public_ordinary_release_packet(project):
    configure(project)
    started=call(project,request('bootstrap',{
        'task':project['task'],'decision':None,'feedback':None,'rework_stage':None,
    }))
    assert started.returncode==0,started.stdout+started.stderr
    start_view=json.loads(started.stdout)
    start_result=json.loads(Path(start_view['response_path']).read_text())
    packet=json.loads((Path(__file__).parents[1]/'ownership/fixtures/public-release.json').read_text())
    packet['input']['request_id']='cli-ordinary-release-1'
    packet['input']['expected_version']=start_result['version']
    released=call(project,packet)
    assert released.returncode==0,released.stdout+released.stderr
    release_view=json.loads(released.stdout)
    release_result=json.loads(Path(release_view['response_path']).read_text())
    assert release_result['status']=='ownership_released'
    assert release_result['task']=='T1'
    assert release_result['after']['task_id'] is None
    assert release_result['after']['worktree_task_id'] is None
    replay=call(project,packet)
    assert replay.returncode==0,replay.stdout+replay.stderr
    replay_view=json.loads(replay.stdout)
    replay_result=json.loads(Path(replay_view['response_path']).read_text())
    assert replay_result['replayed'] is True


def test_small_output_cap_blocks_before_bootstrap_effect(project):
    configure(project);project['cfg']['limits']['output_chars']=1
    project['config_path'].write_text(json.dumps(project['cfg']))
    r=call(project,request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    assert r.returncode==2
    assert not (project['root']/'state/worktrees/T1').exists()


def test_full_batch_demo_generates_all_scopes_and_counts_each_turn_once(tmp_path):
    root=Path(__file__).resolve().parents[2]
    out=tmp_path/'batch-demo'
    r=subprocess.run([sys.executable,str(root/'examples/batch_demo.py'),'--directory',str(out)],
         env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=90)
    assert r.returncode==0,r.stdout+r.stderr
    result=json.loads((out/'batch-demo-report.json').read_text())
    assert result['task_status']=='completed' and result['messages']['user_messages_count']==5
    assert result['messages']['delivered_iterations_count']==4
    assert len(result['artifact_paths'])==2 and all(Path(p).is_file() for p in result['artifact_paths'])
