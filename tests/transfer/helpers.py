from copy import deepcopy
from pathlib import Path
import json
import subprocess
from conftest import write_json
from batch.helpers import request
from harness.runtime import Harness
from harness.application.work import WorkTools


def settings():
    return {'directory':'transfers','archive':'work.zip','manifest':'manifest.json',
            'database':'snapshot.sqlite','files_directory':'files','bundles_directory':'bundles',
            'max_tasks':1000,'max_files':10000,'max_file_bytes':32*1024*1024,
            'max_total_bytes':128*1024*1024,'chunk_bytes':65536,'file_mode':0o600}


def enabled(project):
    project['cfg']['runtime_services']['transfer']=settings()
    write_json(project['config_path'],project['cfg'])
    return project


def destination(project,root):
    root=Path(root);root.mkdir()
    repo=root/'application'
    subprocess.run(['git','clone',str(project['remote']),str(repo)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'checkout','main'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'remote','rename','origin','backup'],check=True,capture_output=True)
    home=root/'harness';home.mkdir()
    cfg=deepcopy(project['cfg']);cfg['git']['repository']=str(repo)
    for name,rel in cfg['processes'].items():
        original=project['root']/rel
        write_json(home/rel,json.loads(original.read_text()))
    path=write_json(home/'project.json',cfg)
    return {'root':home,'app':repo,'cfg':cfg,'config_path':path,'task':deepcopy(project['task'])}


def export(tools,ids=None,sprint=None,request_id='export-1',handoff=None):
    return tools.invoke(request('transfer',{'action':'export','request_id':request_id,
                        'task_ids':ids,'sprint_id':sprint,'handoff':handoff}))


def restore(tools,path,sha,request_id='import-1'):
    return tools.invoke(request('transfer',{'action':'import','request_id':request_id,
                         'package_path':str(path),'package_digest':sha}))


def pick(tools,tid):
    return tools.invoke(request('bootstrap',{'task':{'id':tid},'decision':None,
                                           'feedback':None,'rework_stage':None}))


def handoff_args(payload=None):
    return {'request_id':'checkpoint-1','reason':'continue at another store',
            'result':payload,'commit_message':'WIP: portable task','artifact_paths':[]}
