"""Transparent test-owned arrangement; public commands own every transition."""

from copy import deepcopy
import json
from pathlib import Path
import sys

from batch.helpers import request, result
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria/process.json"


def criteria(requirement="Return twice the input."):
    value = json.loads((FIXTURE.parent / "criteria.json").read_text(encoding="utf-8"))
    value["requirements"] = [requirement]
    return value


def boot(project, *, initial_execution=False, custom_design=False, custom_decision=False,
         empty_grants=False, second_execution=False, output_gate=False, input_gate=False,
         inspection_decision=False, empty_decide_grant=False, with_proof=False):
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
    if inspection_decision:
        observation = process["stages"][1]
        observation.update(handler="inspect", role="arbiter", read_only=True, allowed_paths=[])
        observation["transitions"] = {"clear": "build", "changes_requested": "sketch"}
        observation["rework_targets"] = ["sketch"]
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
    if empty_decide_grant:
        process["stages"][1]["criteria_authority"] = {"propose": [], "decide": []}
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
    if with_proof:
        support = json.loads((FIXTURE.parent / "proof-support.json").read_text())
        method = support["method"]
        method["argv"][0] = sys.executable
        task["methods"] = [method]
        task["method_inputs"] = [{
            "method_id": "CRITERIA-PROOF", "repository_inputs": [], "future_outputs": [],
            "reference_profile": {"runner": "python", "parser": "inline-no-path-arguments", "version": 1},
        }]
        task["checks"]["build"] = ["CRITERIA-PROOF"]
        task["evidence_plan"]["build"]["arguments"] = [{
            "id": "BUILD-PROOF", "kind": "logical", "phase": "continue",
            "observation_methods": ["CRITERIA-PROOF"],
        }]
        # A second criterion explicitly consumes the original observation, rather
        # than constructing an unrelated empty proof book.
        task["evidence_plan"]["build_next"]["arguments"] = [{
            "id": "NEXT-PROOF", "kind": "logical", "phase": "prepare",
            "observation_methods": ["CRITERIA-PROOF"],
        }]
    if output_gate:
        task["content_contract"]["requirements"] = [{
            "id": "build-output", "kind": "artifact", "stages": ["build"],
            "phase": "post", "scope": "task", "pattern": "outputs/build.txt",
            "minimum": 1, "maximum": 1,
            "source": {"kind": "stage_output", "producer_stage": "build"},
        }]
    if input_gate:
        task["content_contract"]["requirements"].append({
            "id": "build-input", "kind": "artifact", "stages": ["build"],
            "phase": "pre", "scope": "task", "pattern": "inputs/required.txt",
            "minimum": 1, "maximum": 1, "source": {"kind": "preexisting"},
        })
        next(item for item in task["stage_contracts"] if item["stage_id"] == "build")["entry_requirements"] = ["build-input"]
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
    if "coverage" in value["stage_work"]:
        value["stage_work"]["coverage"] = "Inspect the exact proposal against the Task goal and declared authority."
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
    context = tools.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    current = tools.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))
    state = current["results"][0]["value"]
    if state["status"] == "verified":
        target = context["workflow"]["next_stage"]
        assert target is not None, "No next stage is declared for this fixture result."
        advanced = tools.invoke(request("advance", {
            "request_id": f"next-{state['stage']}-{state['iteration']}-{state['version']}",
            "task_id": "T1", "target_stage": target,
        }))
        assert advanced["status"] == "progression_target_reached", advanced
    return tools.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))


def assert_unstarted(execution):
    assert set(execution) == {"state", "epoch", "bound_revision"}
    assert execution["state"] == "unstarted"
    assert type(execution["epoch"]) is int and execution["epoch"] >= 0
    assert execution["bound_revision"] is None


def handoff(tools, identifier):
    response = tools.invoke(request("handoff", {
        "request_id": identifier, "reason": "Transfer the real fixture Task to its next configured actor.",
        "result": None, "commit_message": "Preserve fixture task handoff",
        "artifact_paths": [],
    }))
    assert response["status"] == "handed_off"
    return response


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


def fail_commit(monkeypatch, predicate):
    """Fail the real SQLite COMMIT after observing tentative owner writes."""
    from poise.infrastructure.sqlite import transaction
    from poise.common import PoiseError

    original = transaction._boundary
    observed = []

    def boundary(database, sql, path, wait, poll):
        if sql == "COMMIT" and not observed and predicate(database):
            observed.append("tentative_owner_writes")
            raise PoiseError("Fixture COMMIT failure after tentative criteria writes.")
        return original(database, sql, path, wait, poll)

    monkeypatch.setattr(transaction, "_boundary", boundary)
    return observed


def verify_with_proof(tools, context):
    support = json.loads((FIXTURE.parent / "proof-support.json").read_text())
    packet = verify_packet(context)
    if context["stage"] == "build":
        pending = tools.invoke(packet)
        assert pending["status"] == "awaiting_continuation", pending
        argument = deepcopy(support["argument"])
        argument["observation_ids"] = [item["id"] for item in pending["checks"]]
        packet = verify_packet(pending["context"])
        packet["input"]["result"]["evidence_work"] = {
            "phase": "continue", "arguments": [argument], "decisions": [],
        }
    else:
        assert context["stage"] == "build_next"
        argument = deepcopy(support["argument"])
        argument["id"] = "NEXT-PROOF"
        argument["observation_ids"] = [item[0] for item in preserved_proof(tools)["evidence"]]
        packet["input"]["result"]["evidence_work"]["arguments"] = [argument]
    response = tools.invoke(packet)
    assert response["status"] == "verified", response
    return response


def preserved_proof(tools):
    """Exact original proof, immutable receipts/bytes, and public availability."""
    with tools.runtime.store.transaction() as database:
        evidence = [tuple(row) for row in database.execute(
            "SELECT id,data FROM evidence WHERE task_id=? AND stage=? ORDER BY id", ("T1", "build"),
        )]
        book = json.loads(database.execute("SELECT data FROM task_proofs WHERE task_id=?", ("T1",)).fetchone()[0])["book"]
        method = database.execute(
            "SELECT data FROM task_methods WHERE task_id=? AND method_id=?", ("T1", "CRITERIA-PROOF"),
        ).fetchone()[0]
    assert evidence
    arguments = [item for item in book["arguments"] if item["id"] == "BUILD-PROOF"]
    batches = [item for item in book["batches"] if item["stage"] == "build"]
    assert arguments and batches
    assert arguments[-1]["body"]["verdict"] == "proved"
    files = {}
    for _, raw in evidence:
        receipt = json.loads(raw)
        assert receipt["passed"] and receipt["capture_complete"] and receipt["source_unchanged"]
        for stream in ("stdout", "stderr"):
            path = Path(receipt[stream])
            files[str(path)] = path.read_bytes()
    public = tools.invoke(request("show", {"queries": [{"id": "proof", "kind": "evidence"}]}))["results"][0]["value"]
    assert all(argument in public["arguments"] for argument in arguments)
    return {"evidence": evidence, "arguments": arguments, "batches": batches, "method": method, "files": files}
