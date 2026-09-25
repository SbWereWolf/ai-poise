#!/usr/bin/env python3
"""Isolated local demo: generate a process, edit once, and execute it. No network."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from demo import create, save, SOURCE
from poise.common import digest
from work_client import WorkClient
from copy import deepcopy


def run(directory: Path):
    # Initial fixture construction, not a user instruction to edit Poise data.
    home=create(directory)
    env = {**{k: v for k, v in os.environ.items() if k not in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING', 'POISE_SESSION')}, 'PYTHONPATH': str(SOURCE / 'src'), 'POISE_CONFIG': str(home / 'project.json'), 'POISE_CALLER_BINDING': str(home / 'goal-demo-caller.json'), 'LANG': 'C.UTF-8'}
    source=home/'config/processes/development.json'
    template=json.loads(source.read_text())
    settings={"schema":"goal-config-editor-1","root":".","database":"state/config-editor.sqlite","lock":"state/config-editor.lock",
       "responses":"state/config-responses","file_mode":420,"json_indent":2,"lock_seconds":2.0,"lock_poll_seconds":0.01,
       "max_changes":2000,"max_input_bytes":1000000,"output_chars":2200,"exit_codes":{"success":0,"rejected":2,"pending":3},
       "processes":{"development":"config/processes/generated.json"},
       "templates":{"development-v1":{"path":"config/processes/development.json","version":"1","digest":digest(template)}}}
    settings_path=home/'goal-editor.json'; save(settings_path,settings)
    cfg=json.loads((home/'project.json').read_text());cfg['processes']['development']='config/processes/generated.json';save(home/'project.json',cfg)
    selection={"id":"development-v1","version":"1","digest":digest(template)}
    def packet(mode,expected,changes,rid,template):
        return {"schema":"goal-config-batch-1","request_id":rid,"mode":mode,"goal_type":"development","expected_revision":expected,"template":template,"changes":changes}
    def call(data):
        result=subprocess.run([sys.executable,'-m','poise','goal-config','--settings',str(settings_path)],
             input=json.dumps(data),text=True,capture_output=True,env=env,timeout=15)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)
        return json.loads(result.stdout)
    generated=call(packet('create',None,[],'demo-create',selection))
    changes=[
       {"op":"put_requirement","value":{"id":"rollback-required","kind":"section","stages":["tests"],"phase":"post","section":"rollback","states":["populated"]}},
       {"op":"put_section","value":{"id":"rollback","template":"Describe rollback.","normalization":"strip","write_stages":["tests"]}},
       {"op":"patch_stage","id":"tests","set":{"instruction":"Write regression test and explicit rollback note."}}]
    edit=packet('update',generated['revision'],changes,'demo-update',None)
    updated=call(edit); replay=call(edit)
    assert replay['replayed'] and updated['revision']==replay['revision']
    assert json.loads(source.read_text())==template
    task = json.loads((home/'task.json').read_text())
    task['stage_contracts'][0]['exit_requirements'] = ['rollback-required']
    save(home/'task.json', task)
    client = WorkClient(env, 30)
    context=client.bootstrap(task); reports=[]
    blocked=False
    for i,sid in enumerate(['tests','test_review','implementation','code_review']):
        if i: context=client.bootstrap(None, decision='continue')
        wt=Path(context['worktree']); payload=deepcopy(context['result_template']); artifact_specs=[]
        payload['sections']['report']=f'Test fixture report for {sid}; not independent review.'
        payload['commit_message']=f'demo: {sid}'
        if sid=='tests':
            (wt/'tests').mkdir()
            (wt/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
            rejected=client.verify(payload, [], expected_exit=1)
            blocked=rejected['status']=='content_requirements_failed'
            assert rejected['content_gate']['phase'] == 'post'
            assert rejected['checks'][0]['actual_exit_code'] == 1
            payload['sections']['rollback']='Restore previous task commit; only test code was changed.'
        if sid=='implementation': (wt/'src/double.py').write_text('def double(n):\n    return n*2\n')
        if sid=='code_review':
            artifact_specs=[{'scope':'task','path':'result.md','source':{'kind':'text','text':'Verified demo: double(2)=4.\n'}}]
        result=client.verify(payload, artifact_specs);assert result['status']=='verified';reports.append(result['stage'])
    assert client.accept()['status']=='completed'
    result={'status':'PASS','created_revision':generated['revision'],'edited_revision':updated['revision'],
            'replayed_without_duplicate':replay['replayed'],'source_template_unchanged':True,
            'new_exit_gate_blocks_incomplete_stage':blocked,'stages':reports,'task_status':'completed'}
    save(directory/'goal-config-report.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    print(json.dumps(run(parser.parse_args().directory.resolve()),ensure_ascii=False,indent=2))
