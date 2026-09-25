import copy
import json
import sqlite3
import sys
from pathlib import Path
import pytest
from conftest import write_json, fill, git
from conftest import Poise
from poise.common import PoiseError
from .helpers import process, inspect, finding, resolution, decision


def setup_project(project, goal):
    cfg=project["cfg"]; cfg["schema"]="ddd-accounting-12"
    cfg["automatic_checks"]=[]
    cfg["processes"]={goal:f"config/processes/{goal}.json"}
    proc=process(goal)
    for s in proc["stages"]:
        # This scenario tests feedback, not separation of independent actors.
        s["role"] = "executor"
        if not s["read_only"]: s["allowed_paths"]=["src/**"] if goal=="development" else ["docs/**"]
    # Deliberately scramble physical order. Entry and edges remain explicit.
    proc["stages"]=[proc["stages"][2],proc["stages"][1],proc["stages"][0],proc["stages"][3]]
    write_json(project["root"]/f"config/processes/{goal}.json",proc)
    write_json(project["config_path"],cfg)
    contract=project["task"];contract["goal_type"]=goal
    probe = ("from src.double import double; assert double(2)==4; print('CHECK_OK')" if goal=="development"
             else "from pathlib import Path; text=Path('docs/guide.md').read_text(); assert text.startswith('# Guide'); print('CHECK_OK')")
    contract["methods"]=[{"id":"TARGETED","argv":[sys.executable,"-B","-c",probe],"cwd":".","environment":{},
      "source_under_test":{"kind":"repository","bindings":[{"kind":"cwd","path":"."}]},
      "verification_plan":{"responsibility":"Verify the target after each configured route stage.",
        "change_surface":["src/**" if goal=="development" else "docs/**"],"red_stages":[],
        "green_stages":[s["id"] for s in proc["stages"]],"red_failure":None},
      "expected_exit_code":0,"stdout_contains":["CHECK_OK"],"stderr_contains":[]}]
    contract["method_inputs"]=[{"method_id":"TARGETED","repository_inputs":[],"future_outputs":[],
      "reference_profile":{"runner":"python","parser":"inline-no-path-arguments","version":1}}]
    contract["checks"]={s["id"]:["TARGETED"] for s in proc["stages"]}
    contract["evidence_plan"]={s["id"]:{"subject_methods":{},"arguments":[],"review_arguments":[]} for s in proc["stages"]}
    contract["stage_contracts"] = [
        {"stage_id": stage["id"], "allowed_paths": list(stage["allowed_paths"]),
         "entry_requirements": [], "exit_requirements": []}
        for stage in proc["stages"]
    ]
    contract["decomposition"] = {"kind": "ordinary", "integration": None,
        "phases": [{"stage": stage["id"], "skills": ["workflow"], "areas": []}
                   for stage in proc["stages"]]}
    write_json(project["task_path"],contract)
    return Poise(project["config_path"],"S1")

def result(ctx,work):
    path=fill(ctx)
    path["stage_work"]=work

def edit(ctx,goal,text):
    path=Path(ctx["worktree"])/("src/double.py" if goal=="development" else "docs/guide.md")
    path.parent.mkdir(parents=True,exist_ok=True)
    content = ("def double(n):\n    return n * 2\n# " + text) if goal=="development" else "# Guide\n"+text
    path.write_text(content)

@pytest.mark.parametrize("goal",["development","documentation"])
def test_short_and_full_feedback_real_git_sqlite(project,goal):
    h=setup_project(project,goal); ctx=h.bootstrap(task_file=project["task_path"])
    assert ctx["stage"]=="draft" and ctx["handler"]=="produce"
    edit(ctx,goal,"version 1\n"); result(ctx,{})
    first=h.verify(); assert first["status"]=="verified" and first["next_stage"]=="audit"
    assert h.show()["stage"]=="draft"
    h.accept(); assert h.show()["status"]=="accepted"
    ctx=h.bootstrap(decision="continue"); assert ctx["stage"]=="audit"
    result(ctx,inspect([finding()])); r=h.verify()
    assert r["stage_outcome"]=="changes_requested"
    assert h.show()["stage"]=="audit"
    for number,outcome in [(1,"rejected"),(2,"accepted")]:
        # Reload proves persistent route counters and feedback, not in-memory state.
        h=Poise(project["config_path"],"S1")
        ctx=h.bootstrap(decision="continue")
        assert ctx["stage"]=="amend" and ctx["iteration"]==number
        assert ctx["workflow"]["feedback"]["open_findings"][0]["id"]=="F1"
        edit(ctx,goal,f"version {number+1}\n")
        result(ctx,{"resolutions":[resolution(f"R{number}")]}); assert h.verify()["status"]=="verified"
        ctx=h.bootstrap(decision="continue"); assert ctx["stage"]=="follow_up"
        result(ctx,inspect(decisions=[decision(f"R{number}",outcome)])); assert h.verify()["status"]=="verified"
    assert h.accept()["status"]=="completed"
    assert h.task_commands.workflow_context("T1")["feedback"]["open_findings"]==[]
    assert h.show()["status"] == "read_only"
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM task_results").fetchone()[0]==6
        assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
    sha=git(Path(ctx["worktree"]),"rev-parse","HEAD")
    assert sha == git(Path(ctx["worktree"]), "rev-parse", "tasks/T1")
    assert git(project["app"],"ls-remote","backup","refs/heads/tasks/T1") == ""
    assert (project["app"]/"src/double.py").read_text()=="def double(n):\n    return n + 1\n"

@pytest.mark.parametrize("goal",["development","documentation"])
def test_short_path_does_not_visit_fix_nodes(project,goal):
    h=setup_project(project,goal);ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,goal,"result\n");result(ctx,{});h.verify()
    ctx=h.bootstrap(decision="continue");result(ctx,inspect());h.verify();h.accept()
    with h.store.transaction() as db:
        assert db.execute("SELECT DISTINCT stage FROM submissions ORDER BY stage").fetchall()
        assert [r[0] for r in db.execute("SELECT DISTINCT stage FROM submissions ORDER BY stage")]==["audit","draft"]


def test_inspect_is_read_only_and_never_accepts_its_own_target_edit(project):
    h=setup_project(project,"development");ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,"development","initial\n"); result(ctx,{});h.verify();ctx=h.bootstrap(decision="continue")
    edit(ctx,"development","modified during review\n");result(ctx,inspect())
    with pytest.raises(PoiseError,match="read-only"): h.verify()
    assert h.show()["status"]=="active"


def test_task_artifact_requirement_uses_terminal_edge_not_last_index(project):
    h=setup_project(project,"documentation")
    contract=project["task"];contract["artifact_requirements"]=[{"scope":"task","pattern":"*.md","minimum":1,"maximum":1}]
    write_json(project["task_path"],contract)
    ctx=h.bootstrap(task_file=project["task_path"]);edit(ctx,"documentation","initial\n");result(ctx,{});h.verify()
    ctx=h.bootstrap(decision="continue");result(ctx,inspect())
    with pytest.raises(PoiseError): h.verify()
    a=Path(ctx["task_root"])/"report.md";a.write_text("final report")
    data=ctx["result_template"];data["artifact_paths"]=[str(a)];
    assert h.verify()["status"]=="verified"


def test_atomic_feedback_save_and_resume(project):
    h=setup_project(project,"documentation");ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,"documentation","initial\n");result(ctx,{});h.verify();ctx=h.bootstrap(decision="continue");result(ctx,inspect([finding()]))
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_feedback BEFORE INSERT ON workflow_layers BEGIN SELECT RAISE(ABORT,'feedback-fault'); END")
    before=h.show()["submission_count"]
    with pytest.raises(sqlite3.IntegrityError,match="feedback-fault"): h.verify()
    assert h.show()["submission_count"]==before
    with h.store.transaction() as db: db.execute("DROP TRIGGER fail_feedback")
    assert h.verify()["status"]=="verified"
    assert h.show()["workflow"]["feedback"]["open_findings"][0]["id"]=="F1"


def test_continue_rejects_changed_verified_subject(project):
    h=setup_project(project,"development");ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,"development","initial\n");result(ctx,{});h.verify()
    edit(ctx,"development","modified after report\n")
    with pytest.raises(PoiseError,match="Commit or code changed after verification"):
        h.bootstrap(decision="continue")
    assert h.show()["stage"]=="draft"


def test_explicit_return_preserves_feedback_and_earlier_reports(project):
    h=setup_project(project,"documentation");ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,"documentation","draft\n");result(ctx,{});first=h.verify()
    first_sub=h.task_queries.section("T1","draft","report",None)["submission"]
    ctx=h.bootstrap(decision="continue");result(ctx,inspect());h.verify()
    ctx=h.bootstrap(decision="rework",feedback="Переработать текст",rework_stage="draft")
    assert ctx["stage"]=="draft" and ctx["iteration"]==2
    assert h.task_queries.result("T1",first_sub)==first
    h=Poise(project["config_path"],"S1")
    assert h.bootstrap()["workflow"]["visits"]["draft"]==2


def test_content_obligation_is_enforced_inside_feedback_route(project):
    h=setup_project(project,"documentation")
    # A produced section is an exit obligation, explicitly selected by the Task.
    project["task"]["content_contract"] = {
        "sections": [{"id":"fix_basis","template":"Заполнить.","normalization":"strip","write_stages":["amend"]}],
        "routes": [],
        "requirements": [{"id":"fix-basis-required","kind":"section","section":"fix_basis","stages":["amend"],"phase":"post","states":["populated"]}],
    }
    next(s for s in project["task"]["stage_contracts"] if s["stage_id"] == "amend")["exit_requirements"] = ["fix-basis-required"]
    write_json(project["task_path"],project["task"])
    ctx=h.bootstrap(task_file=project["task_path"])
    edit(ctx,"documentation","draft\n");result(ctx,{})
    h.verify()
    ctx=h.bootstrap(decision="continue");result(ctx,inspect([finding()]));h.verify()
    ctx=h.bootstrap(decision="continue");result(ctx,{"resolutions":[resolution()]})
    blocked=h.verify();assert blocked["status"]=="content_requirements_failed"
    assert blocked["content_gate"]["phase"] == "post"
    assert len(blocked["checks"]) == 1
    assert blocked["checks"][0]["actual_exit_code"] == 0
    assert h.show()["status"] == "active"
    payload=ctx["result_template"];payload["sections"]["fix_basis"]="Связь исправления с требованием обоснована."
    assert h.verify()["status"]=="verified"


def test_old_step02_schema_rejected_without_touching_store(project):
    from poise.infrastructure.sqlite.database import Database
    path=project["root"]/"old3.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE precious(value TEXT)")
        db.execute("INSERT INTO precious VALUES('keep')")
        db.execute("PRAGMA user_version=3")
    original=path.read_bytes()
    with pytest.raises(PoiseError,match="миграц"):
        Database(path,project["root"]/"old3.lock",2,0.01)
    assert path.read_bytes()==original
