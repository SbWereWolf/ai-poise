"""The first-stage self-pilot runs actual setup tests, not a toy app implementation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tests.conftest import git


def test_real_source_pilot_does_not_auto_accept_or_change_target(tmp_path):
    source=Path(__file__).resolve().parents[1]
    repo=tmp_path/'poise-source';repo.mkdir()
    for folder in ('src','tests','config'):
        shutil.copytree(source/folder,repo/folder,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    shutil.copy2(source/'pyproject.toml',repo/'pyproject.toml')
    # Ignore transient pytest bytecode, the same way as the real target project.
    shutil.copy2(source/'.gitignore',repo/'.gitignore')
    git(repo,'init','-b','main');git(repo,'config','user.name','Pilot Fixture')
    git(repo,'config','user.email','pilot@example.invalid')
    git(repo,'add','.');git(repo,'commit','-m','Actual source snapshot for setup pilot')
    base=git(repo,'rev-parse','HEAD')
    command=[sys.executable,str(source/'examples/project_pilot.py'),'--poise-root',str(repo),
        '--repository',str(repo),'--base-ref','main','--destination','state/project-pilot',
        '--task-id','SOURCE-PILOT']
    result=subprocess.run(command,cwd=source,env={**os.environ,'PYTHONPATH':str(source/'src')},
                          capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stderr+result.stdout
    report=json.loads(result.stdout)
    assert report['status']=='PASS' and report['task_state']=='verified'
    assert report['stage']=='planning' and report['user_acceptance']=='not_performed'
    assert report['repeat_replayed'] and not report['target_changed']
    assert report['target_revision']==base
    observations=report['report']['evidence']['observations']
    assert len(observations)==1 and observations[0]['actual_exit_code']==0
    assert git(repo,'rev-parse','main')==base
