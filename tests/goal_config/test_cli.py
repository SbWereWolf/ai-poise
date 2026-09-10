import json
import os
import subprocess
import sys
from pathlib import Path
from tests.goal_config.helpers import settings, request, additions


def test_cli_one_stdin_packet_creates_config_without_task_session(tmp_path):
    cfg, selection=settings(tmp_path)
    raw=request("create","writing",None,additions(),selection=selection)
    env={k:v for k,v in os.environ.items() if k not in ("HARNESS_CONFIG","HARNESS_SESSION")}
    r=subprocess.run([sys.executable,"-m","harness","goal-config","--settings",str(cfg)],input=json.dumps(raw),text=True,capture_output=True,env=env,timeout=10)
    assert r.returncode==0, r.stderr+r.stdout
    out=json.loads(r.stdout)
    assert out["status"]=="applied"
    assert Path(out["config_path"]).is_file()


def test_cli_reports_multiple_errors_in_one_bounded_response(tmp_path):
    cfg, selection=settings(tmp_path)
    changes=[{"op":"patch_stage","id":f"missing-{i}","set":{"instruction":"x"}} for i in range(30)]
    r=subprocess.run([sys.executable,"-m","harness","goal-config","--settings",str(cfg)],input=json.dumps(request("create","writing",None,changes,selection=selection)),text=True,capture_output=True,timeout=10)
    assert r.returncode==2, r.stdout+r.stderr
    out=json.loads(r.stdout)
    assert out["error_count"]==30 and len(r.stdout)<=2000
    full=json.loads(Path(out["response_path"]).read_text())
    assert len(full["issues"])==30


def test_cli_duplicate_keys_and_nan_are_rejected(tmp_path):
    cfg, _=settings(tmp_path)
    for text in ('{"schema":"goal-config-batch-1","schema":"other"}', '{"x":NaN}'):
        r=subprocess.run([sys.executable,"-m","harness","goal-config","--settings",str(cfg)],input=text,text=True,capture_output=True,timeout=10)
        assert r.returncode==2


def test_unusable_output_budget_rejected_before_business_publication(tmp_path):
    cfg, selection=settings(tmp_path)
    data=json.loads(cfg.read_text()); data['output_chars']=1; cfg.write_text(json.dumps(data))
    r=subprocess.run([sys.executable,'-m','harness','goal-config','--settings',str(cfg)],
        input=json.dumps(request('create','writing',None,[],selection=selection)),text=True,capture_output=True,timeout=10)
    assert r.returncode!=0
    assert not (tmp_path/'config/processes/writing.json').exists()
    assert 'output_chars' in r.stdout+r.stderr
