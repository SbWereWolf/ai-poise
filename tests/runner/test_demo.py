"""Same four CLI routes, independent cases so each has a bounded test duration."""
from pathlib import Path
import json
import os
import subprocess
import sys
import pytest


@pytest.mark.parametrize('goal',['development','documentation'])
@pytest.mark.parametrize('feedback',[False,True])
def test_four_documented_cli_routes(tmp_path,goal,feedback):
    root=Path(__file__).resolve().parents[2]
    directory=tmp_path/'runner-demo'
    program=('import json,sys; from pathlib import Path; from runner_demo import run_one; '
             'print(json.dumps(run_one(Path(sys.argv[1]),sys.argv[2],sys.argv[3]=="true")))')
    result=subprocess.run([sys.executable,'-c',program,str(directory),goal,str(feedback).lower()],
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(root/'src'),str(root/'examples')])},
        capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((directory/'runner-report.json').read_text())
    assert report['status']=='PASS' and report['main_unchanged'] and report['remote_unchanged'] and report['task_status']=='completed'
    assert report['outcomes'].count('changes_requested')==(2 if feedback else 0)
