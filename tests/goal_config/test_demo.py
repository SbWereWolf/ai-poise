import json
import os
import subprocess
import sys
from pathlib import Path


def test_generated_config_short_route_red_green_and_explicit_gate(tmp_path):
    root=Path(__file__).resolve().parents[2]
    r=subprocess.run([sys.executable,str(root/'examples/goal_config_demo.py'),'--directory',str(tmp_path/'demo')],
        env={**os.environ,'PYTHONPATH':str(root/'src')},text=True,capture_output=True,timeout=40)
    assert r.returncode==0,r.stdout+r.stderr
    report=json.loads((tmp_path/'demo/goal-config-report.json').read_text())
    assert report['status']=='PASS' and report['new_gate_blocked_before_tests']
    assert report['replayed_without_duplicate'] and report['source_template_unchanged']
