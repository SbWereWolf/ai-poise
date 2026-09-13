from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conftest import WorkPoise as Poise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from batch.helpers import request


def _feature():
    required = (
        "initialize_stage_contracts",
        "revise_stage_contract",
        "stage_contract_context",
    )
    missing = [name for name in required if not hasattr(Poise, name)]
    assert not missing, f"runtime StageContract API is missing: {missing}"


def _contracts(process, *, first_entry=(), first_scope=None):
    values = []
    for index, stage in enumerate(process["stages"]):
        values.append({
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"] if index or first_scope is None else first_scope),
            "entry_requirements": list(first_entry if index == 0 else ()),
            "exit_requirements": [],
        })
    return values


def _configure(project, *, contracts=True, entry_required=False, exit_required=False,
               first_scope=None, inspection=False):
    process = deepcopy(project["process"])
    task = deepcopy(project["task"])
    if inspection:
        inspect = deepcopy(process["stages"][1])
        revise = deepcopy(process["stages"][2])
        inspect["transitions"] = {"clear": None, "changes_requested": revise["id"]}
        inspect["rework_targets"] = [revise["id"]]
        revise["transitions"] = {"complete": inspect["id"]}
        revise["rework_targets"] = [revise["id"]]
        process["stages"] = [inspect, revise]
        process["route"]["entry"] = inspect["id"]
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage["id"]: [] for stage in process["stages"]}
        task["evidence_plan"] = {
            stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in process["stages"]
        }
        project["cfg"]["automatic_checks"] = []
        write_json(project["config_path"], project["cfg"])
    requirements = []
    if entry_required:
        requirements.append({
                "id": "entry-input",
                "kind": "artifact",
                "stages": [process["stages"][0]["id"]],
                "phase": "pre",
                "scope": "task",
                "pattern": "inputs/ready.txt",
                "minimum": 1,
                "maximum": 1,
                "source": {"kind": "preexisting"},
            })
    if exit_required:
        requirements.append({
            "id": "stage-output",
            "kind": "artifact",
            "stages": [process["stages"][0]["id"]],
            "phase": "post",
            "scope": "task",
            "pattern": "outputs/result.txt",
            "minimum": 1,
            "maximum": 1,
            "source": {"kind": "stage_output", "producer_stage": process["stages"][0]["id"]},
        })
    task["content_contract"] = {"sections": [], "routes": [], "requirements": requirements}
    if contracts:
        task["stage_contracts"] = _contracts(
            process,
            first_entry=("entry-input",) if entry_required else (),
            first_scope=first_scope,
        )
        if exit_required:
            task["stage_contracts"][0]["exit_requirements"] = ["stage-output"]
    write_json(project["root"] / "config/processes/development.json", process)
    return process, task


def _bootstrap(project, session="executor", **options):
    _feature()
    _, task = _configure(project, **options)
    tools = WorkTools(Poise(project["config_path"], session))
    result = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return tools, result, task


def _counts(runtime, task_id):
    tables = ("submissions", "journal", "artifacts", "action_runs", "task_results")
    with runtime.store.transaction() as db:
        counts = {}
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            where = " WHERE task_id=?" if "task_id" in columns else ""
            args = (task_id,) if where else ()
            counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
        row = db.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    return counts, tuple(row) if row is not None else None


def _legacy(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        row = db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        metadata = json.loads(row[0])
        metadata["contract"].pop("stage_contracts")
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")), task_id),
        )


def _seed_artifact(runtime, task_id="T1"):
    root = Path(runtime.state) / runtime.paths["tasks"] / task_id
    path = root / "artifacts" / "inputs" / "ready.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ready\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_id = "fixture-entry-input"
    with runtime.store.transaction() as db:
        db.execute(
            "INSERT INTO artifacts(id,owner,scope,path,digest) VALUES(?,?,?,?,?)",
            (artifact_id, task_id, "task", str(path), digest),
        )
        db.execute("INSERT INTO task_artifacts(task_id,artifact_id) VALUES(?,?)", (task_id, artifact_id))
    return path


def _transition(runtime, action, *, request_id, task_id="T1", expected_version,
                contracts=None, stage_id=None, contract=None, role="creator"):
    if action == "initialize":
        return runtime.initialize_stage_contracts(
            task_id=task_id,
            expected_version=expected_version,
            request_id=request_id,
            contracts=contracts,
            reason="Explicit stage contract transition",
            authorization={"role": role},
        )
    return runtime.revise_stage_contract(
        task_id=task_id,
        expected_version=expected_version,
        request_id=request_id,
        stage_id=stage_id,
        contract=contract,
        reason="Reviewer corrected the Task-specific gate",
        authorization={"role": role},
    )


def test_entry_failure_precedes_claim_and_context(project):
    tools, result, _ = _bootstrap(project, entry_required=True)
    assert result["status"] == "content_requirements_failed"
    assert result["content_requirements"]["phase"] == "pre"
    assert tools.runtime.current_task() is None
    with tools.runtime.store.transaction() as db:
        row = db.execute("SELECT status,claimed_by FROM tasks WHERE id='T1'").fetchone()
    assert tuple(row) == ("available", None)


def test_entry_recheck_precedes_submission_and_artifact_effects(project):
    first, rejected, task = _bootstrap(project, entry_required=True)
    assert rejected["status"] == "content_requirements_failed"
    runtime = first.runtime
    path = _seed_artifact(runtime)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": task["id"]}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    before = _counts(runtime, "T1")
    path.unlink()
    with pytest.raises(PoiseError, match="entry"):
        tools.invoke(request("verify", {
            "result": context["result_template"],
            "artifacts": [{
                "scope": "task",
                "path": "should-not-exist.txt",
                "source": {"kind": "text", "text": "no side effect"},
            }],
        }))
    assert _counts(runtime, "T1") == before


def test_entry_rejection_preserves_all_pre_effect_counters(project):
    tools, result, _ = _bootstrap(project, entry_required=True)
    counts, state = _counts(tools.runtime, "T1")
    assert result["status"] == "content_requirements_failed"
    assert state[0:4] == ("available", 0, 1, None)
    assert counts["submissions"] == counts["artifacts"] == counts["action_runs"] == counts["task_results"] == 0


def test_exit_gate_sees_stage_outputs_before_verification(project):
    tools, context, _ = _bootstrap(project, exit_required=True)
    draft = deepcopy(context["result_template"])
    draft["sections"]["report"] = "Produced the declared output."
    draft["commit_message"] = "test: stage output"
    result = tools.invoke(request("verify", {
        "result": draft,
        "artifacts": [{
            "scope": "task",
            "path": "outputs/result.txt",
            "source": {"kind": "text", "text": "ready"},
        }],
    }))
    assert result["status"] == "verified"


def test_legacy_task_requires_explicit_stage_contract_initialization(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    runtime.handoff({
        "request_id": "legacy-release",
        "reason": "exercise legacy reload",
        "result": None,
        "commit_message": "WIP: legacy fixture",
        "artifact_paths": [],
    })
    resumed = WorkTools(Poise(project["config_path"], "next"))
    with pytest.raises(PoiseError, match="stage_contract_transition_required"):
        resumed.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
        }))
    assert task["stage_contracts"]


def test_stage_contract_initialization_replays_and_rejects_conflict(project):
    tools, context, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    version = runtime.task_queries.record("T1")["version"]
    first = _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                        contracts=task["stage_contracts"])
    replay = _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                         contracts=task["stage_contracts"])
    assert {k: v for k, v in first.items() if k != "replayed"} == {
        k: v for k, v in replay.items() if k != "replayed"
    }
    assert replay["replayed"] is True
    changed = deepcopy(task["stage_contracts"])
    changed[0]["allowed_paths"] = ["different/**"]
    with pytest.raises(PoiseError, match="request.*conflict"):
        _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                    contracts=changed)


def test_reviewer_revision_replays_and_records_old_new_history(project):
    tools, _, task = _bootstrap(project, inspection=True)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][0])
    contract["allowed_paths"] = []
    version = runtime.task_queries.record("T1")["version"]
    first = _transition(runtime, "revise", request_id="revise-1", expected_version=version,
                        stage_id=contract["stage_id"], contract=contract, role="reviewer")
    restarted = Poise(project["config_path"], "executor")
    replay = _transition(restarted, "revise", request_id="revise-1", expected_version=version,
                         stage_id=contract["stage_id"], contract=contract, role="reviewer")
    history = restarted.stage_contract_context("T1")["history"]
    assert replay["replayed"] is True
    assert first["old"] != first["new"]
    assert history[-1]["old"] == first["old"] and history[-1]["new"] == first["new"]
    assert history[-1]["reason"] and history[-1]["authorization"] == {"role": "reviewer"}


def test_executor_and_non_inspection_stage_cannot_revise_contract(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][0])
    version = runtime.task_queries.record("T1")["version"]
    with pytest.raises(PoiseError, match="inspection.*reviewer"):
        _transition(runtime, "revise", request_id="bad-revise", expected_version=version,
                    stage_id=contract["stage_id"], contract=contract, role="executor")


def test_reopen_preserves_initialized_stage_contract(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    version = runtime.task_queries.record("T1")["version"]
    _transition(runtime, "initialize", request_id="init-reopen", expected_version=version,
                contracts=task["stage_contracts"])
    runtime.handoff({
        "request_id": "reopen-release",
        "reason": "Reload initialized contracts",
        "result": None,
        "commit_message": "WIP: contract persistence",
        "artifact_paths": [],
    })
    restarted = Poise(project["config_path"], "reopener")
    WorkTools(restarted).invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert restarted.stage_contract_context("T1")["current"] == task["stage_contracts"]


def test_task_specific_scope_narrows_and_expands_template_enforcement(project):
    tools, context, _ = _bootstrap(project, first_scope=["tests/exact/**", "docs/task/**"])
    scope = tools.runtime.stage_contract_context("T1")["current"][0]["allowed_paths"]
    assert scope == ["tests/exact/**", "docs/task/**"]
    assert "tests/**" not in scope
