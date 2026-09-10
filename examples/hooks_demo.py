#!/usr/bin/env python3
"""Native-hook wire replay with a generated launcher and a real local task.

No live Codex/IDE/Gmail is claimed. The hook JSON payloads and user/reviewer
messages are fixtures. Task commands, Git, SQLite and generated scripts are real.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from demo import create,save,SOURCE


def fill_template(value,parameters):
    if isinstance(value,str):
        for k,v in parameters.items():value=value.replace('${'+k+'}',v)
        return value
    if isinstance(value,list):return [fill_template(x,parameters) for x in value]
    if isinstance(value,dict):return {k:fill_template(v,parameters) for k,v in value.items()}
    return value


def run(directory):
    home=create(directory)
    project=json.loads((home/'project.json').read_text())
    project['batch']['message_source']={'id':'codex-hook-main','mode':'runtime_event'}
    save(home/'project.json',project)
    parameters={'python':sys.executable,'source_root':str(SOURCE/'src'),'workspace_parent':str(directory)}
    cfg=fill_template(json.loads((SOURCE/'config/hook-templates/settings.json').read_text()),parameters)
    plan=fill_template(json.loads((SOURCE/'config/hook-templates/codex-main.json').read_text()),parameters)
    settings=home/'hooks-settings.json'
    env={**os.environ,'PYTHONPATH':str(SOURCE/'src')}
    def cli(args,packet):
        p=subprocess.run([sys.executable,'-B','-m','harness',*args],input=json.dumps(packet),
                         text=True,capture_output=True,env=env,timeout=45)
        if p.returncode:raise RuntimeError(p.stdout+p.stderr)
        obj=json.loads(p.stdout)
        return json.loads(Path(obj['response_path']).read_text()) if 'response_path' in obj else obj
    installed=cli(['runtime-setup','--settings',str(settings),'--max-input-bytes',str(1048576)],
        {'settings':cfg,'installation':{'request_id':'demo-install','expected_revision':None,'definition':plan}})
    assert installed['trust_status']=='requires_user_review' and installed['capability_checks']['ready']
    hooks=json.loads((home/'.codex/hooks.json').read_text())['hooks']
    def event(name,turn=None):
        payload={'session_id':'hook-demo','transcript_path':None,'cwd':str(home),'hook_event_name':name}
        if name=='SessionStart':payload['source']='startup'
        elif name=='UserPromptSubmit':payload.update(turn_id=turn,prompt='Synthetic user message; do not store this body')
        elif name=='Stop':payload.update(turn_id=turn,stop_hook_active=False,last_assistant_message='Synthetic report')
        elif name=='SessionEnd':payload['reason']='other'
        command=hooks[name][0]['hooks'][0]['command']
        p=subprocess.run(command,shell=True,input=json.dumps(payload),text=True,capture_output=True,env=env,timeout=15)
        if p.returncode:raise RuntimeError(p.stderr)
        return json.loads(p.stdout)
    start=event('SessionStart')
    from harness.infrastructure.hook_transport import HookService
    binding=HookService(settings).latest_binding('hook-demo','primary')
    def work(operation,inputs):
        p=subprocess.run([binding['launcher']],input=json.dumps({'operation':operation,'input':inputs,'messages':[]}),
                         text=True,capture_output=True,env=env,timeout=45)
        if p.returncode:raise RuntimeError(p.stdout+p.stderr)
        obj=json.loads(p.stdout)
        return json.loads(Path(obj['response_path']).read_text()) if 'response_path' in obj else obj
    task=json.loads((home/'task.json').read_text());reports=[]
    for i,stage in enumerate(('tests','test_review','implementation','code_review')):
        event('UserPromptSubmit',f'user-{i}')
        event('UserPromptSubmit',f'user-{i}') # repeated delivery is not a new message
        ctx=work('bootstrap',{'task':task if i==0 else None,'decision':None if i==0 else 'continue','feedback':None,'rework_stage':None})
        wt=Path(ctx['worktree'])
        if stage=='tests':
            (wt/'tests').mkdir()
            (wt/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2), 4)\n')
        if stage=='implementation':(wt/'src/double.py').write_text('def double(n):\n    return n * 2\n')
        payload=deepcopy(ctx['result_template'])
        payload['sections']['report']='Fixture report: '+stage
        payload['commit_message']='demo: '+stage
        artifacts=[] if stage!='code_review' else [{'scope':'task','path':'result.md','source':{'kind':'text','text':'RED and GREEN observed; native hook replay only.'}}]
        report=work('verify',{'result':payload,'artifacts':artifacts});assert report['status']=='verified'
        stop=event('Stop',f'user-{i}');assert 'decision' not in stop
        assert work('show',{'queries':[{'id':'messages','kind':'messages'}]})['interaction']['observed_messages_count']==i+1
        reports.append(report)
    event('UserPromptSubmit','accept');final=work('accept',{})
    event('SessionEnd')
    assert final['status']=='completed' and final['interaction']['observed_messages_count']==5
    assert not any(p.name=='config.toml' for p in (home/'.codex').rglob('*'))
    report={'status':'PASS','task_status':final['status'],'reports':len(reports),
        'messages':final['interaction']['observed_messages_count'],'red_exit':reports[0]['checks'][0]['actual_exit_code'],
        'green_exit':reports[2]['checks'][0]['actual_exit_code'],'launcher':binding['launcher'],
        'codex_trust':'not_modified','live_codex':'not_tested',
        'source':'native wire fixtures replayed through generated command; real local WorkTools/Git/SQLite'}
    save(directory/'hooks-demo-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().directory.resolve()),ensure_ascii=False,indent=2))
