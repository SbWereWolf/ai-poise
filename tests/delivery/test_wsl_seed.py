import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

EXECUTABLE_OBLIGATIONS = {
    "0001": [
        *(f"requirements[{index}]" for index in range(10)),
        "definition_of_done[0]",
        "definition_of_done[2]",
        "definition_of_done[3]",
    ],
    "0002": [
        *(f"requirements[{index}]" for index in (1, 2, 3, 4, 5, 6, 8)),
        "definition_of_done[0]",
    ],
}

LEGACY_BASELINE_CONTRACTS = {
    "0001": {
        "argv": ["python3.13", "-m", "pytest", "tests/projects", "-q"],
        "responsibility": (
            "Guard the pre-existing project and requirements baseline before Task-produced changes."
        ),
    },
    "0002": {
        "argv": [
            "python3.13", "-m", "pytest", "tests/hook_transport",
            "tests/runtime_services", "-q",
        ],
        "responsibility": (
            "Guard the pre-existing hook transport and runtime service baseline before Task-produced changes."
        ),
    },
}


def task_definitions():
    root=ROOT/"delivery/task-definitions/harness"
    return {task_id:json.loads((root/f"{task_id}.json").read_text()) for task_id in ("0001","0002","0003")}


def expected_stage_contracts(blueprint, task):
    process=json.loads((ROOT/blueprint["process_sources"][task["goal_type"]]["path"]).read_text())
    requirements=process["content_contract"]["requirements"]+task["content_contract"]["requirements"]
    return [{
        "stage_id":stage["id"],
        "allowed_paths":stage["allowed_paths"],
        "entry_requirements":[item["id"] for item in requirements if item["phase"]=="pre" and stage["id"] in item["stages"]],
        "exit_requirements":[item["id"] for item in requirements if item["phase"]=="post" and stage["id"] in item["stages"]],
    } for stage in process["stages"]]

def git_repo(path):
    path.mkdir(); subprocess.run(["git","init","-b","main",str(path)],check=True,capture_output=True)
    (path/"README.md").write_text("seed test\n")
    for relative in ("tests/projects", "tests/hook_transport", "tests/runtime_services"):
        target=path/relative;target.mkdir(parents=True);(target/"test_seed.py").write_text("def test_seed(): pass\n")
    subprocess.run(["git","-C",str(path),"add","."],check=True)
    subprocess.run(["git","-C",str(path),"-c","user.name=Seed Test","-c","user.email=seed@example.invalid","commit","-m","baseline"],check=True,capture_output=True)

def project_from_blueprint(tmp_path):
    blueprint=json.loads((ROOT/"config/project-templates/wsl-poise.json").read_text())
    home=tmp_path/"poise-config"; home.mkdir()
    config=blueprint["config"]; config["project"]="poise"
    repo=tmp_path/"poise-repo"; git_repo(repo)
    config["git"].update(repository=str(repo),base_ref="main",remote="origin",author_name="Seed Test",author_email="seed@example.invalid",push_required=False)
    config["paths"]["state"]=str((tmp_path/"poise-project-data").resolve())
    for goal,rel in config["processes"].items():
        source=ROOT/blueprint["process_sources"][goal]["path"]
        target=home/rel; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(source.read_bytes())
    path=home/"project.json"; path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+"\n"); return path

def test_wsl_seed_creates_single_poise_project_sprint(tmp_path):
    blueprint=json.loads((ROOT/"config/project-templates/wsl-poise.json").read_text())
    definitions=task_definitions()
    for task_id, task in definitions.items():
        assert task["stage_contracts"]==expected_stage_contracts(blueprint,task)
        if task_id in EXECUTABLE_OBLIGATIONS:
            assert task["executable_obligations"]==EXECUTABLE_OBLIGATIONS[task_id]
        else:
            assert task["goal_type"]=="documentation"
            assert "executable_obligations" not in task
        if task_id in LEGACY_BASELINE_CONTRACTS:
            baseline=next(method for method in task["methods"] if method["id"]=="BASELINE")
            expected=LEGACY_BASELINE_CONTRACTS[task_id]
            assert baseline["argv"]==expected["argv"]
            assert baseline["expected_exit_code"]==0
            assert baseline["verification_plan"]["responsibility"]==expected["responsibility"]
            assert task["checks"]["baseline"]==["BASELINE"]
            assert task["checks"]["test_inspection"]==[]
            assert task["evidence_plan"]["baseline"]["subject_methods"]=={
                "BASELINE":{"exit_codes":[0],"stdout_contains":[],"stderr_contains":[]}}
            assert baseline["verification_plan"]["green_stages"]==["baseline"]
            assert baseline["verification_plan"]["change_surface"]==[]
    config=project_from_blueprint(tmp_path)
    result=subprocess.run([sys.executable,str(ROOT/"tools/seed_wsl_tasks.py"),"--poise-config",str(config)],env={**os.environ,"PYTHONPATH":str(ROOT/"src")},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(result.stdout); assert report["status"]=="seeded"
    db=tmp_path/"poise-project-data/tasks.sqlite"
    assert db.is_file()
    with sqlite3.connect(db) as connection:
        assert [r[0] for r in connection.execute("SELECT id FROM tasks ORDER BY id")]==["0001","0002","0003"]
        assert connection.execute("SELECT state FROM sprints WHERE id='SPRINT-0001'").fetchone()[0]=="published"
        for task_id, metadata in connection.execute("SELECT id, metadata FROM tasks ORDER BY id"):
            stored=json.loads(metadata)["contract"]
            for field in ("goal","requirements","definition_of_done"):
                assert stored[field]==definitions[task_id][field]
        sprint=json.loads(connection.execute("SELECT data FROM sprints WHERE id='SPRINT-0001'").fetchone()[0])
        assert sprint["aggregate"]["plan"]["dependencies"]==[
            {"predecessor":"0001","successor":"0002","kind":"result"},
            {"predecessor":"0002","successor":"0003","kind":"result"},
        ]
    assert report["poise"]["eligible"]==["0001"]
    assert {x["task"] for x in report["poise"]["blocked"]}=={"0002","0003"}
    bootstrap=tmp_path/"poise-project-data/artifacts/sprints/SPRINT-0001/artifacts/requirements-bootstrap.json"
    seed=json.loads(bootstrap.read_text()); assert seed["schema"]=="requirements-bootstrap-1" and len(seed["system_requirements"])==2

def test_single_wsl_template_has_project_local_storage_and_no_system_split():
    blueprint=json.loads((ROOT/"config/project-templates/wsl-poise.json").read_text())
    paths=blueprint["config"]["paths"]
    assert paths["database"]=="tasks.sqlite"
    assert paths["standalone_tasks"]=="artifacts/standalone"
    assert paths["sprints"]=="artifacts/sprints"
    assert not (ROOT/"config/project-templates/wsl-system.json").exists()
    assert not (ROOT/"delivery/task-definitions/system").exists()
