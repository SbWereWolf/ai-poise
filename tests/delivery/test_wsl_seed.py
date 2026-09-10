import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

def git_repo(path):
    path.mkdir(); subprocess.run(["git","init","-b","main",str(path)],check=True,capture_output=True)
    (path/"README.md").write_text("seed test\n")
    subprocess.run(["git","-C",str(path),"add","."],check=True)
    subprocess.run(["git","-C",str(path),"-c","user.name=Seed Test","-c","user.email=seed@example.invalid","commit","-m","baseline"],check=True,capture_output=True)

def project_from_blueprint(tmp_path):
    blueprint=json.loads((ROOT/"config/project-templates/wsl-harness.json").read_text())
    home=tmp_path/"harness-config"; home.mkdir()
    config=blueprint["config"]; config["project"]="harness"
    repo=tmp_path/"harness-repo"; git_repo(repo)
    config["git"].update(repository=str(repo),base_ref="main",remote="origin",author_name="Seed Test",author_email="seed@example.invalid",push_required=False)
    config["paths"]["state"]=str((tmp_path/"harness-project-data").resolve())
    for goal,rel in config["processes"].items():
        source=ROOT/blueprint["process_sources"][goal]["path"]
        target=home/rel; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(source.read_bytes())
    path=home/"project.json"; path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+"\n"); return path

def test_wsl_seed_creates_single_harness_project_sprint(tmp_path):
    config=project_from_blueprint(tmp_path)
    result=subprocess.run([sys.executable,str(ROOT/"tools/seed_wsl_tasks.py"),"--harness-config",str(config)],env={**os.environ,"PYTHONPATH":str(ROOT/"src")},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(result.stdout); assert report["status"]=="seeded"
    db=tmp_path/"harness-project-data/tasks.sqlite"
    assert db.is_file()
    with sqlite3.connect(db) as connection:
        assert [r[0] for r in connection.execute("SELECT id FROM tasks ORDER BY id")]==["0001","0002","0003"]
        assert connection.execute("SELECT state FROM sprints WHERE id='SPRINT-0001'").fetchone()[0]=="published"
    assert report["harness"]["eligible"]==["0001"]
    assert {x["task"] for x in report["harness"]["blocked"]}=={"0002","0003"}
    bootstrap=tmp_path/"harness-project-data/artifacts/sprints/SPRINT-0001/artifacts/requirements-bootstrap.json"
    seed=json.loads(bootstrap.read_text()); assert seed["schema"]=="requirements-bootstrap-1" and len(seed["system_requirements"])==2

def test_single_wsl_template_has_project_local_storage_and_no_system_split():
    blueprint=json.loads((ROOT/"config/project-templates/wsl-harness.json").read_text())
    paths=blueprint["config"]["paths"]
    assert paths["database"]=="tasks.sqlite"
    assert paths["tasks"]=="artifacts/tasks"
    assert paths["sprints"]=="artifacts/sprints"
    assert not (ROOT/"config/project-templates/wsl-system.json").exists()
    assert not (ROOT/"delivery/task-definitions/system").exists()
