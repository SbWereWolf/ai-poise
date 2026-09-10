import os
from pathlib import Path
import subprocess
import sys
import json


def test_actual_cli_demo_and_source_labelling(tmp_path):
    root=Path(__file__).resolve().parents[2]
    r=subprocess.run([sys.executable,'examples/accounting_demo.py','--directory',str(tmp_path/'demo')],cwd=root,
        env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=90)
    assert r.returncode==0,r.stdout+r.stderr
    result=json.loads(r.stdout.splitlines()[-1]);assert result['status']=='PASS'
    report=json.loads(Path(result['report']).read_text());assert report['token_events_are_synthetic'] is True
    assert report['economics']['totals']['model_tokens']==6000
