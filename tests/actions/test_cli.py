import json
import os
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.mark.parametrize('scenario',['integration_conflict','environment_feedback'])
def test_documented_real_cli_routes(tmp_path,scenario):
    root=Path(__file__).resolve().parents[2];target=tmp_path/'scenario'
    r=subprocess.run([sys.executable,str(root/'examples/actions_demo.py'),'--directory',str(target),'--scenario',scenario],
        env={**os.environ,'PYTHONPATH':str(root/'src')},text=True,capture_output=True,timeout=60)
    assert r.returncode==0,r.stdout+r.stderr
    report=json.loads((target/'actions-demo-report.json').read_text())
    assert report['status']=='PASS' and report['task_status']=='completed'
    assert report['review_is_fixture']
    if scenario=='integration_conflict':
        assert any(c['status']=='awaiting_action_continuation' for c in report['calls'])
        assert report['reports'][-1]['handler']=='publish'
    else:assert [r['handler'] for r in report['reports']]==['apply_plan','inspect','apply_plan','inspect']
