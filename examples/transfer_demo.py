#!/usr/bin/env python3
"""Two independent stores/repositories, one preserved task. No network or live connectors."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from demo import create, save, SOURCE
from work_client import WorkClient


def run(directory):
    if directory.exists():raise ValueError('Choose a new demonstration directory')
    directory.mkdir(parents=True)
    source=create(directory/'source')
    config=json.loads((source/'project.json').read_text())
    target=directory/'destination';target.mkdir()
    repository=target/'application'
    remote=subprocess.check_output(['git','-C',config['git']['repository'],'remote','get-url',config['git']['remote']],text=True).strip()
    subprocess.run(['git','clone','--branch','main',remote,str(repository)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repository),'remote','rename','origin',config['git']['remote']],check=True,capture_output=True)
    target_home=target/'harness';target_home.mkdir()
    for rel in config['processes'].values():
        path=target_home/rel;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,path)
    target_config=deepcopy(config);target_config['git']['repository']=str(repository)
    save(target_home/'project.json',target_config)
    def client(home,session):
        return WorkClient({**os.environ,'PYTHONPATH':str(SOURCE/'src'),
             'HARNESS_CONFIG':str(home/'project.json'),'HARNESS_SESSION':session},30)
    a=client(source,'source-agent');b=client(target_home,'receiving-agent')
    task=json.loads((source/'task.json').read_text())
    current=a.invoke('bootstrap',{'task':task,'decision':None,'feedback':None,'rework_stage':None})
    tree=Path(current['worktree']);(tree/'tests').mkdir()
    (tree/'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
    artifact=a.invoke('artifacts',{'items':[{'scope':'task','path':'handoff-note.md',
             'source':{'kind':'text','text':'Preserved test-first work; no result has been accepted.'}}]})
    payload=deepcopy(current['result_template']);payload['sections']['report']='Prepared regression before verification.'
    payload['artifact_paths']=artifact['artifact_paths']
    exported=a.invoke('transfer',{'action':'export','request_id':'demo-export','task_ids':None,'sprint_id':None,
       'handoff':{'request_id':'demo-checkpoint','reason':'Continue on another machine',
        'result':payload,'commit_message':'WIP: regression test','artifact_paths':[]}})
    received=b.invoke('transfer',{'action':'import','request_id':'demo-import','package_path':exported['package_path'],
                                 'package_digest':exported['package_digest']})
    assert received['status']=='imported'
    ctx=b.invoke('bootstrap',{'task':{'id':task['id']},'decision':None,'feedback':None,'rework_stage':None})
    assert ctx['stage']==current['stage'] and ctx['iteration']==current['iteration']
    assert ctx['worktree']!=current['worktree']
    assert ctx['result_template']['sections']['report']==payload['sections']['report']
    assert Path(ctx['result_template']['artifact_paths'][0]).is_relative_to(target_home)
    reports=[]
    for i,sid in enumerate(('tests','test_review','implementation','code_review')):
        if i:
            ctx=b.invoke('bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None})
        assert ctx['stage']==sid
        if sid=='implementation':(Path(ctx['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
        result=deepcopy(ctx['result_template']);result['sections']['report']=f'Fixture inspection/work result for {sid}, not independent review.'
        result['commit_message']=f'demo: {sid}'
        artifacts=[] if sid!='code_review' else [{'scope':'task','path':'result.md',
            'source':{'kind':'text','text':'Transferred WIP continued through RED, GREEN and fixture reviews.'}}]
        report=b.invoke('verify',{'result':result,'artifacts':artifacts});assert report['status']=='verified';reports.append(report)
    final=b.invoke('accept',{});assert final['status']=='completed'
    full=b.invoke('show',{'queries':[{'id':'raw','kind':'tool_result','receipt_id':reports[0]['checks'][0]['id'],
                                      'representation':'full','range':None}]})
    out={'status':'PASS','final_status':final['status'],'same_stage_and_iteration':True,
         'different_store_and_repository':True,'source_task_not_accepted_by_export':True,
         'reports':len(reports),'red_exit':reports[0]['checks'][0]['actual_exit_code'],
         'green_exit':reports[2]['checks'][0]['actual_exit_code'],
         'full_output_bytes_after_cleanup':full['results'][0]['value']['total_bytes'],
         'export':exported,'import':received,'source':'local fixtures and local bare remote, not external delivery'}
    save(directory/'transfer-demo-report.json',out)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().directory.resolve()),ensure_ascii=False,indent=2))
