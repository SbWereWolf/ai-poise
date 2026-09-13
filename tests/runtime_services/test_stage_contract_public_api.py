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
    tools = WorkTools(Poise(project["config_path"], session))
    context = tools.invoke(request("bootstrap", {
        "task": _configure(project, scope=scope, inspection=inspection),
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
    for field in tuple(valid["input"]):
        malformed = deepcopy(valid)
        malformed["input"].pop(field)
        with pytest.raises(PoiseError):
            tools.invoke(malformed)
        assert tools.runtime.task_queries.record("T1")["version"] == version
    malformed = deepcopy(valid)
    malformed["input"]["unexpected"] = True
    with pytest.raises(PoiseError):
        tools.invoke(malformed)
    assert tools.runtime.task_queries.record("T1")["version"] == version
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
    restarted = WorkTools(Poise(project["config_path"], "creator"))
    replay = restarted.invoke(packet)
    assert replay["replayed"] is True and replay["version"] == first["version"]
    conflict = deepcopy(packet)
    conflict["input"]["contracts"][0]["allowed_paths"] = ["conflict/**"]
    with pytest.raises(PoiseError, match="request.*conflict"):
        restarted.invoke(conflict)


def test_public_revise_requires_inspection_owner_authorization_and_version(project):
    tools, context = _boot(project)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    contract = deepcopy(task["stage_contracts"][0])
    contract["allowed_paths"] = ["tests/exact/**"]
    packets = [
        _revise("T1", current["version"], context["stage"], contract, role="executor"),
        _revise("T1", current["version"] + 1, context["stage"], contract),
    ]
    for packet in packets:
        with pytest.raises(PoiseError):
            tools.invoke(packet)
    assert tools.runtime.task_queries.record("T1")["version"] == current["version"]


def test_public_revise_replay_survives_restart_and_records_history(project):
    tools, context = _boot(project, inspection=True)
    task = _configure(project)
    current = tools.runtime.task_queries.record("T1")
    stage_id = tools.runtime.current_task()["stage"]
    contract = next(deepcopy(item) for item in task["stage_contracts"] if item["stage_id"] == stage_id)
    packet = _revise("T1", current["version"], stage_id, contract, request_id="durable-revision")
    first = tools.invoke(packet)
    restarted = WorkTools(Poise(project["config_path"], "creator"))
    replay = restarted.invoke(packet)
    history = restarted.runtime.stage_contract_context("T1")["history"]
    assert replay["replayed"] is True and replay["version"] == first["version"]
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


def test_public_scope_contract_controls_verify_paths(project):
    tools, context = _boot(project, scope=["tests/exact/**", "docs/task/**"])
    worktree = context["worktree"]
    outside = __import__("pathlib").Path(worktree) / "tests" / "outside.py"
    outside.parent.mkdir(exist_ok=True)
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    draft = deepcopy(context["result_template"])
    draft["sections"]["report"] = "Scope should reject a template-only path."
    draft["commit_message"] = "test: scope"
    with pytest.raises(PoiseError, match="allowed_paths"):
        tools.invoke(request("verify", {"result": draft, "artifacts": []}))
