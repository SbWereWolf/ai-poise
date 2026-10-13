#!/usr/bin/env python3
"""Render a fresh workspace-recover session without reusing archived identity.

This command generates files only. The declared recovery flow restores bytes and
runtime; public Task ownership inspection and legitimate acquisition remain explicit.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

HERE=Path(__file__).resolve().parent

def _json(path:Path,data:dict)->None:
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def initialize(*,run:Path,destination:Path,parts:Path,checkpoint:Path,checksum:str,
               tool:Path,python:Path,drive_folder:str,recipient:str)->dict:
    """Create a new run directory and immutable-input restoration recipe."""
    run=run.absolute();destination=destination.absolute()
    parts=parts.resolve();checkpoint=checkpoint.resolve();tool=tool.resolve();python=python.absolute()
    if run.is_relative_to(HERE):raise ValueError('Run directory cannot be inside the source kit')
    if run.exists() or run.is_symlink():raise ValueError('Run directory must be new')
    if destination.exists() or destination.is_symlink():raise ValueError('Restore target must be new')
    if not re.fullmatch(r'[0-9a-f]{64}',checksum):raise ValueError('Expected exact SHA-256')
    if not parts.is_dir() or not checkpoint.is_file():raise ValueError('Recovery inputs are missing')
    if not python.is_file():raise ValueError('Explicit Python executable is missing')
    if not (tool/'bin/workspace-recover.mjs').is_file():raise ValueError('workspace-recover CLI is missing')
    if json.loads((tool/'package.json').read_text())['version']!='1.0.0':raise ValueError('workspace-recover 1.0.0 is required')
    if not drive_folder.strip() or not recipient.strip() or '\n' in recipient or '\r' in recipient:
        raise ValueError('Verified delivery destination is required')
    # This UUID is a unique filename suffix, never an invented Poise Session ID.
    binding=destination/'poise-op/operator/profile'/f'cloud-{uuid.uuid4().hex}-caller.json'
    run.mkdir(parents=True,exist_ok=False)
    for name in ('flows','evidence','packets','checkpoints','connector-results','sessions'):
        (run/name).mkdir()
    shutil.copytree(HERE,run/'kit',ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    config={'schema':'poise-cloud-run/v1','parts_directory':str(parts),'checkpoint':str(checkpoint),
            'checkpoint_sha256':checksum,'destination':str(destination),'python':str(python),
            'workspace_recover_root':str(tool),'backup_policy':{'min_seconds':600,'max_seconds':900},
            'delivery':{'google_drive_parent':drive_folder,'gmail_recipient':recipient},
            'bootstrap_environment':{'git_identity_from_manifest':True},'caller_binding':str(binding),
            'caller_binding_scope':'fresh path only; the public identity owner creates the Session; never reuse archived binding'}
    _json(run/'RUN.json',config)
    flow={'schema':'workspace-recover/flow/v3','workspace':str(run/'restore-workspace'),'steps':[
      {'id':'restore-saved-workspace','op':'command','argv':[str(python),str(run/'kit/recovery/restore_continuation.py'),
       '--parts-directory',str(parts),'--checkpoint',str(checkpoint),'--checkpoint-sha256',checksum,
       '--destination',str(destination),'--install-operator','--python',str(python)],
       'timeoutMs':420000,'onError':'stop'}]}
    _json(run/'flows/restore.flow.json',flow)
    return {'run_config':str(run/'RUN.json'),'flow':str(run/'flows/restore.flow.json'),
            'execute':['node',str(tool/'bin/workspace-recover.mjs'),'flow','run','--manifest',str(run/'flows/restore.flow.json'),
                       '--session',str(run/'restore-session')],'task_lifecycle_started':False}

def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run','destination','parts','checkpoint','tool','python'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('checksum','drive-folder','recipient'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    try:
        print(json.dumps(initialize(run=a.run,destination=a.destination,parts=a.parts,checkpoint=a.checkpoint,
              checksum=a.checksum,tool=a.tool,python=a.python,drive_folder=a.drive_folder,recipient=a.recipient),indent=2))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as e:
        print(json.dumps({'status':'rejected','error':str(e)}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
