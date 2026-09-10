from pathlib import Path
import json
import os
import subprocess
import sys
import pytest


@pytest.mark.parametrize('scenario,commands,args,decisions',[
    ('command_negative',1,0,0),('logical',0,1,1),('mixed',1,1,1),('observe_feedback',2,2,2)])
def test_documented_evidence_cli_routes(tmp_path,scenario,commands,args,decisions):
    root=Path(__file__).resolve().parents[2]
    out=tmp_path/'evidence-demo'
    result=subprocess.run([sys.executable,str(root/'examples/evidence_demo.py'),'--directory',str(out),'--scenario',scenario],
                          env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads((out/'evidence-report.json').read_text())
    assert data['status']=='PASS' and data['task_status']=='completed'
    assert (data['command_executions'],data['arguments'],data['decisions'])==(commands,args,decisions)
    assert data['raw_output_survives_cleanup'] is True
    if scenario=='command_negative': assert data['stage_outcomes'][0]=='not_satisfied'
