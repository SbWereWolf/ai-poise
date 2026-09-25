import json
import os
from pathlib import Path
import subprocess
import sys
from .helpers import enabled
from batch.helpers import request


def test_transfer_cli_rejects_unknown_operation_without_worktree(project):
    enabled(project)
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
         'POISE_CONFIG':str(project['config_path']),'POISE_CALLER_BINDING':str(project['root']/'transfer-cli.caller.json')}
    p=subprocess.run([sys.executable,'-m','poise','work'],
         input=json.dumps(request('transfer',{'action':'unknown'})),text=True,capture_output=True,env=env,timeout=15)
    assert p.returncode==2 and json.loads(p.stdout)['status']=='rejected'
    assert not (project['root']/'state/worktrees/T1').exists()


def test_transfer_cli_end_to_end_demo(tmp_path):
    root=Path(__file__).resolve().parents[2];target=tmp_path/'demo'
    p=subprocess.run([sys.executable,str(root/'examples/transfer_demo.py'),'--directory',str(target)],
          env={**os.environ,'PYTHONPATH':str(root/'src')},text=True,capture_output=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr
    report=json.loads((target/'transfer-demo-report.json').read_text())
    assert report['final_status']=='completed' and report['reports']==4
    assert report['red_exit']==1 and report['green_exit']==0
    assert report['full_output_bytes_after_acceptance']>0
    assert report['terminal_read_did_not_claim_task']
