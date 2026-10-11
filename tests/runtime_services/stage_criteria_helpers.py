"""Transparent test-owned arrangement; public commands own every transition."""

from copy import deepcopy
import json
from pathlib import Path

from batch.helpers import request, result
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria/process.json"


def criteria(requirement="Return twice the input."):
    return {
        "requirements": [requirement],
        "definition_of_ready": ["The intended behavior is specified."],
        "definition_of_done": ["The required behavior is demonstrated."],
    }


def boot(project, *, initial_execution=False, custom_design=False, custom_decision=False,
         empty_grants=False, second_execution=False, output_gate=False):
    process = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if initial_execution:
        build = deepcopy(process["stages"][2])
        build["transitions"] = {"complete": None}
        build["rework_targets"] = ["build"]
        process["stages"] = [build]
        process["route"]["entry"] = "build"
    if custom_design:
        process["stages"][0]["purpose"] = "other"
    if custom_decision:
        process["stages"][1]["purpose"] = "other"
    if second_execution:
        another = deepcopy(process["stages"][2])
        another["id"] = "build_next"
        another["rework_targets"] = ["build_next", "sketch"]
        process["stages"][2]["transitions"]["complete"] = "build_next"
        process["stages"].insert(3, another)
        for stage in process["stages"]:
            for grant in ("propose", "decide"):
                if stage["criteria_authority"][grant]:
                    stage["criteria_authority"][grant].append("build_next")
    if empty_grants:
        process["stages"][0]["criteria_authority"] = {"propose": [], "decide": []}
    task = deepcopy(project["task"])
    stages = process["stages"]
    task["methods"] = []
    task["method_inputs"] = []
    task["checks"] = {s["id"]: [] for s in stages}
    task["evidence_plan"] = {
        s["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
        for s in stages
    }
    task["decomposition"]["phases"] = [
        {"stage": s["id"], "skills": ["task-domain"], "areas": []}
        for s in stages
    ]
    task["stage_contracts"] = [
        {
            "stage_id": s["id"], "allowed_paths": list(s["allowed_paths"]),
            "entry_requirements": [], "exit_requirements": [],
            "criteria": criteria(),
        }
        for s in stages
    ]
    if output_gate:
        task["content_contract"]["requirements"] = [{
            "id": "build-output", "kind": "artifact", "stages": ["build"],
            "phase": "post", "scope": "task", "pattern": "outputs/build.txt",
            "minimum": 1, "maximum": 1,
            "source": {"kind": "stage_output", "producer_stage": "build"},
        }]
    project["cfg"]["automatic_checks"] = []
    write_json(project["root"] / "config/processes/development.json", process)
    write_json(project["config_path"], project["cfg"])
    tools = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
    context = tools.invoke(request("bootstrap", {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }))
    return tools, context


def show(tools, stage="build"):
    response = tools.invoke(request("show", {"queries": [
        {"id": "criteria", "kind": "stage_criteria", "stage_id": stage},
    ]}))
    assert response["status"] == "read_only"
    assert [item["id"] for item in response["results"]] == ["criteria"]
    return response["results"][0]["value"]


def version(tools):
    response = tools.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))
    return response["results"][0]["value"]["version"]


def verify_packet(context, work=None):
    value = result(context, "The criteria operation and its result are recorded.")
    if work is not None:
        value["stage_work"]["criteria_work"] = deepcopy(work)
    return request("verify", {"result": value, "artifacts": []})


def proposal(tools, target="build", requirement="Return twice every integer input."):
    state = show(tools, target)
    return {
        "stage_id": target,
        "expected_criteria_revision": state["revision"],
        "criteria": criteria(requirement),
        "entry_requirements": [], "exit_requirements": [],
    }


def work(tools, request_id, *, proposals=(), decisions=()):
    return {
        "request_id": request_id, "expected_version": version(tools),
        "proposals": list(proposals), "decisions": list(decisions),
    }


def next_stage(tools):
    return tools.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))


def rows(tools):
    """Observe authoritative Task data, not incidental telemetry/refusal receipts."""
    with tools.runtime.store.transaction() as database:
        return {
            "task": [tuple(row) for row in database.execute(
                "SELECT status,stage_index,iteration,version,metadata FROM tasks WHERE id=?", ("T1",),
            )],
            "events": [tuple(row) for row in database.execute(
                "SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq", ("T1",),
            )],
            "submissions": [tuple(row) for row in database.execute(
                "SELECT stage,iteration,digest,data FROM submissions WHERE task_id=? ORDER BY seq", ("T1",),
            )],
        }
