from copy import deepcopy
import json

import pytest

from conftest import WorkPoise as Poise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.work.domain import parse_request
from batch.helpers import request


def _feature():
    from poise.modules.work import domain

    operations = getattr(domain, "STAGE_CONTRACT_OPERATIONS", ())
    assert set(operations) == {"initialize_stage_contracts", "revise_stage_contract"}, (
        "public packet grammar must declare the two exact StageContract transitions"
    )


def _contracts(project, *, scope=None):
    values = []
    for index, stage in enumerate(project["process"]["stages"]):
        values.append({
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"] if scope is None or index else scope),
            "entry_requirements": [],
            "exit_requirements": [],
        })
    return values


def _configure(project, *, scope=None, inspection=False):
    if inspection:
        process = deepcopy(project["process"])
        inspect = deepcopy(process["stages"][1])
        revise = deepcopy(process["stages"][2])
        inspect["handler"] = "inspect"
        inspect["transitions"] = {"clear": None, "changes_requested": revise["id"]}
        inspect["rework_targets"] = [revise["id"]]
        revise["transitions"] = {"complete": inspect["id"]}
        revise["rework_targets"] = [revise["id"]]
        process["stages"] = [inspect, revise]
        process["route"]["entry"] = inspect["id"]
        project["process"] = process
        project["cfg"]["automatic_checks"] = []
        write_json(project["config_path"], project["cfg"])
        write_json(project["root"] / "config/processes/development.json", process)
    task = deepcopy(project["task"])
    if inspection:
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage["id"]: [] for stage in project["process"]["stages"]}
        task["evidence_plan"] = {
            stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in project["process"]["stages"]
        }
    task["stage_contracts"] = _contracts(project, scope=scope)
    return task


def _boot(project, session="creator", *, scope=None, inspection=False):
    _feature()
    task = _configure(project, scope=scope, inspection=inspection)
    tools = WorkTools(Poise(project["config_path"], session))
    context = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return tools, context


def _legacy(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        row = db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        metadata = json.loads(row[0])
        metadata["contract"].pop("stage_contracts")
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")), task_id),
        )


def _snapshot(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        task = tuple(db.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
            "FROM tasks WHERE id=?", (task_id,)
        ).fetchone())
        events = tuple(tuple(row) for row in db.execute(
            "SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
        ))
        journal = tuple(tuple(row) for row in db.execute(
            "SELECT session_id,event,data FROM journal WHERE task_id=? ORDER BY seq", (task_id,)
        ))
        submissions = tuple(tuple(row) for row in db.execute(
            "SELECT seq,stage,iteration,digest,data FROM submissions "
            "WHERE task_id=? ORDER BY seq",
            (task_id,),
        ))
    return task, events, journal, submissions


def _initialize(task, version, request_id="initialize-public"):
    return request("initialize_stage_contracts", {
        "request_id": request_id,
        "task_id": task["id"],
        "expected_version": version,
        "contracts": task["stage_contracts"],
        "reason": "Initialize an exact legacy Task contract",
        "authorization": {"role": "creator"},
    })


def _revise(task_id, version, stage_id, contract, request_id="revise-public", role="reviewer"):
    return request("revise_stage_contract", {
        "request_id": request_id,
        "task_id": task_id,
        "expected_version": version,
        "stage_id": stage_id,
        "contract": contract,
        "reason": "Reviewer narrows the exact Task scope",
        "authorization": {"role": role},
    })


def test_public_initialize_packet_is_exact_and_atomic(project):
    tools, _ = _boot(project)
    task = _configure(project)
    _legacy(tools.runtime)
    version = tools.runtime.task_queries.record("T1")["version"]
    valid = _initialize(task, version)
    assert parse_request(valid, project["cfg"]["batch"]) == valid
    before = _snapshot(tools.runtime)
    for field in tuple(valid["input"]):
        malformed = deepcopy(valid)
        malformed["input"].pop(field)
        with pytest.raises(PoiseError):
            tools.invoke(malformed)
        assert _snapshot(tools.runtime) == before
    malformed = deepcopy(valid)
    malformed["input"]["unexpected"] = True
    with pytest.raises(PoiseError):
        tools.invoke(malformed)
    assert _snapshot(tools.runtime) == before
    result = tools.invoke(valid)
    assert result["status"] == "stage_contracts_initialized"
    assert result["version"] == version + 1 and result["replayed"] is False


def test_public_initialize_replay_survives_restart_and_rejects_conflict(project):
    tools, _ = _boot(project)
    task = _configure(project)
    _legacy(tools.runtime)
    version = tools.runtime.task_queries.record("T1")["version"]
    packet = _initialize(task, version, "durable-init")
    first = tools.invoke(packet)
    after = _snapshot(tools.runtime)
    restarted = WorkTools(Poise(project["config_path"], "creator"))
    replay = restarted.invoke(packet)
    assert replay == {**first, "replayed": True}
    assert _snapshot(restarted.runtime) == after
    wrong_version = deepcopy(packet)
    wrong_version["input"]["expected_version"] += 999
    with pytest.raises(PoiseError, match="request.*conflict"):
        restarted.invoke(wrong_version)
    malformed_version = deepcopy(packet)
    malformed_version["input"]["request_id"] = "bad-version-type"
    malformed_version["input"]["expected_version"] = True
    with pytest.raises(PoiseError, match="expected_version"):
        restarted.invoke(malformed_version)
    assert _snapshot(restarted.runtime) == after
    conflict = deepcopy(packet)
    conflict["input"]["contracts"][0]["allowed_paths"] = ["conflict/**"]
    with pytest.raises(PoiseError, match="request.*conflict"):
        restarted.invoke(conflict)
    assert _snapshot(restarted.runtime) == after


def test_public_revise_requires_inspection_owner_authorization_and_version(project):
    tools, context = _boot(project)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    contract = deepcopy(task["stage_contracts"][0])
    contract["allowed_paths"] = ["tests/exact/**"]
    before = _snapshot(tools.runtime)
    with pytest.raises(PoiseError, match="inspection"):
        tools.invoke(_revise("T1", current["version"], context["stage"], contract))
    assert _snapshot(tools.runtime) == before


def test_public_revise_requires_reviewer_role_on_inspection(project):
    tools, context = _boot(project, inspection=True)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    contract = deepcopy(task["stage_contracts"][0])
    before = _snapshot(tools.runtime)
    with pytest.raises(PoiseError, match="reviewer"):
        tools.invoke(_revise(
            "T1", current["version"], context["stage"], contract, role="executor",
        ))
    assert _snapshot(tools.runtime) == before


def test_public_revise_rejects_stale_version_on_inspection(project):
    tools, context = _boot(project, inspection=True)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    contract = deepcopy(task["stage_contracts"][0])
    before = _snapshot(tools.runtime)
    with pytest.raises(PoiseError, match="version"):
        tools.invoke(_revise(
            "T1", current["version"] + 1, context["stage"], contract,
        ))
    assert _snapshot(tools.runtime) == before


def test_public_revise_replay_survives_restart_and_records_history(project):
    tools, context = _boot(project, inspection=True)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    contract = deepcopy(task["stage_contracts"][1])
    contract["allowed_paths"] = ["docs/review-fix/**"]
    packet = _revise("T1", current["version"], contract["stage_id"], contract, request_id="durable-revision")
    first = tools.invoke(packet)
    after = _snapshot(tools.runtime)
    restarted = WorkTools(Poise(project["config_path"], "creator"))
    replay = restarted.invoke(packet)
    history = restarted.runtime.stage_contract_context("T1")["history"]
    assert replay == {**first, "replayed": True}
    assert _snapshot(restarted.runtime) == after
    wrong_version = deepcopy(packet)
    wrong_version["input"]["expected_version"] += 999
    with pytest.raises(PoiseError, match="request.*conflict"):
        restarted.invoke(wrong_version)
    malformed_version = deepcopy(packet)
    malformed_version["input"]["request_id"] = "bad-revision-version-type"
    malformed_version["input"]["expected_version"] = False
    with pytest.raises(PoiseError, match="expected_version"):
        restarted.invoke(malformed_version)
    assert _snapshot(restarted.runtime) == after
    assert history[-1]["request_id"] == "durable-revision"
    assert {"old", "new", "reason", "authorization"} <= set(history[-1])


def test_public_entry_gate_failure_returns_projection_without_claim(project):
    _feature()
    task = _configure(project)
    first = project["process"]["stages"][0]["id"]
    task["content_contract"]["requirements"] = [{
        "id": "required-input",
        "kind": "artifact",
        "stages": [first],
        "phase": "pre",
        "scope": "task",
        "pattern": "inputs/required.txt",
        "minimum": 1,
        "maximum": 1,
        "source": {"kind": "preexisting"},
    }]
    task["stage_contracts"][0]["entry_requirements"] = ["required-input"]
    tools = WorkTools(Poise(project["config_path"], "creator"))
    result = tools.invoke(request("bootstrap", {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert result["status"] == "content_requirements_failed"
    assert result["stage"] == first
    assert result["content_requirements"]["phase"] == "pre"
    assert tools.runtime.current_task() is None


def test_public_entry_gate_evaluates_only_task_contract_refs(project):
    task = _configure(project)
    first = project["process"]["stages"][0]["id"]
    task["content_contract"]["requirements"] = [
        {
            "id": "selected-empty",
            "kind": "artifact",
            "stages": [first],
            "phase": "pre",
            "scope": "task",
            "pattern": "selected/**",
            "minimum": 0,
            "maximum": 0,
            "source": {"kind": "preexisting"},
        },
        {
            "id": "unselected-missing",
            "kind": "artifact",
            "stages": [first],
            "phase": "pre",
            "scope": "task",
            "pattern": "missing/**",
            "minimum": 1,
            "maximum": 1,
            "source": {"kind": "preexisting"},
        },
    ]
    task["stage_contracts"][0]["entry_requirements"] = ["selected-empty"]
    tools = WorkTools(Poise(project["config_path"], "creator"))
    result = tools.invoke(request("bootstrap", {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert result["status"] == "active"
    assert [item["id"] for item in result["content_requirements"]["due"]] == [
        "selected-empty",
    ]


def test_public_scope_contract_controls_verify_paths(project):
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    project["task"]["methods"] = []
    project["task"]["method_inputs"] = []
    project["task"]["checks"] = {
        stage["id"]: [] for stage in project["process"]["stages"]
    }
    project["task"]["evidence_plan"] = {
        stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
        for stage in project["process"]["stages"]
    }
    tools, context = _boot(project, scope=["tests/exact/**", "docs/task/**"])
    Path = __import__("pathlib").Path
    worktree = Path(context["worktree"])
    outside = worktree / "tests" / "outside.py"
    outside.parent.mkdir(exist_ok=True)
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    draft = deepcopy(context["result_template"])
    draft["sections"]["report"] = "Scope should reject a template-only path."
    draft["commit_message"] = "test: scope"
    before = _snapshot(tools.runtime)
    with pytest.raises(PoiseError, match="allowed_paths"):
        tools.invoke(request("verify", {"result": draft, "artifacts": []}))
    assert _snapshot(tools.runtime) == before
    outside.unlink()
    accepted = worktree / "docs" / "task" / "accepted.md"
    accepted.parent.mkdir(parents=True, exist_ok=True)
    accepted.write_text("Task-only expansion.\n", encoding="utf-8")
    result = tools.invoke(request("verify", {"result": draft, "artifacts": []}))
    assert result["status"] == "verified"
