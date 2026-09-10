import json
import os
from pathlib import Path
import subprocess
import sys


def test_runtime_cli_demo(project,tmp_path):
    source=Path(__file__).resolve().parents[2]
    run=subprocess.run([sys.executable,str(source/'examples/runtime_demo.py'),'--directory',str(tmp_path/'runtime-demo')],
        env={**os.environ,'PYTHONPATH':str(source/'src')},capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((tmp_path/'runtime-demo/runtime-demo-report.json').read_text())
    assert report['final_status']=='completed' and report['reports']==4
    assert report['messages']['user_messages_count']==7
    assert report['red_exit']==1 and report['green_exit']==0
    assert report['full_read_after_cleanup']>0 and report['handoff']['verified'] is False
