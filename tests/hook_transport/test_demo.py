import json,os,subprocess,sys
from pathlib import Path


def test_generated_native_hooks_and_bound_work_complete_task(tmp_path):
    source=Path(__file__).resolve().parents[2]
    p=subprocess.run([sys.executable,str(source/'examples/hooks_demo.py'),'--directory',str(tmp_path/'demo with spaces')],
        env={**os.environ,'PYTHONPATH':str(source/'src')},text=True,capture_output=True,timeout=100)
    assert p.returncode==0,p.stdout+p.stderr
    result=json.loads((tmp_path/'demo with spaces/hooks-demo-report.json').read_text())
    assert result['status']=='PASS' and result['task_status']=='completed'
    assert result['messages']==5 and result['reports']==4
    assert result['red_exit']==1 and result['green_exit']==0
    assert result['codex_trust']=='not_modified' and result['live_codex']=='not_tested'
