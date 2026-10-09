"""Transparent public lifecycle arrangement; literal expected values stay in tests."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from sprints.helpers import setup, task, draft, publish, bootstrap

PAYLOAD = b"customer result\n"
PROOF = b"temporary proof must disappear\n"


def arrange(project, member=False, identifier="T1", before_verify=None,
            with_checks=False, artifact_files=()):
    setup(project)
    tools = WorkTools(WorkPoise(project["config_path"], "material-owner"))
    contract = task(project, identifier)
    contract["methods"] = []
    contract["method_inputs"] = []
    contract["checks"] = {"work": []}
    if with_checks:
        from sprints.helpers import task as checked_task
        checked = checked_task(project, identifier,
            command="exec(open('src/proof_check.py').read())")
        contract["methods"] = checked["methods"]
        contract["methods"][0]["argv"] = [sys.executable, "-B", "-m", "pytest",
            "-q", "-s", "src/test_delivery_proof.py"]
        contract["methods"][0]["outputs"] = [
            {"id": "raw-proof", "path": "raw-proof.bin", "required": True}]
        contract["method_inputs"] = checked["method_inputs"]
        contract["method_inputs"][0]["future_outputs"] = [
            {"path": "src/test_delivery_proof.py", "producer_stage": "work"}]
        contract["method_inputs"][0]["reference_profile"] = {
            "runner": "pytest", "parser": "positional-paths", "version": 1}
        contract["checks"] = {"work": ["CHECK"]}
    if member:
        created = draft(tools, [contract])
        publish(tools, created["revision"])
        context = bootstrap(tools, identifier)
    else:
        contract["sprint_id"] = None
        context = tools.invoke(request("bootstrap", {
            "task": contract, "decision": None, "feedback": None, "rework_stage": None,
        }))
    root = Path(context["task_root"])
    worktree = Path(context["worktree"])
    (worktree / "src" / "delivered.py").write_text("VALUE = 'agreed result'\n")
    if with_checks:
        (worktree / "src" / "test_delivery_proof.py").write_bytes(
            (Path(__file__).parent / "fixtures" / "subject_check.py").read_bytes())
    if before_verify is not None:
        before_verify(tools, context)
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "The agreed result exists."
    result["commit_message"] = "Create an agreed customer result"
    output = tools.invoke(request("verify", {"result": result, "artifacts": list(artifact_files)}))
    assert output["status"] == "verified"
    proof = root / "artifacts" / "proof.txt"
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes(PROOF)
    source = root / "artifacts" / "result.txt"
    source.write_bytes(PAYLOAD)
    return tools, root, source, output["commit"]


def delivery_packet(tools, disposition, identifier="T1", request_id="agree-materials"):
    # Version is an optimistic input, never the expected lifecycle outcome.
    record = tools.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))
    value = record["results"][0]["value"]
    version = value["revision"] if value["status"] == "newborn" else value["version"]
    packet = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["agreement"]
    packet.update(request_id=request_id, task_id=identifier, expected_version=version,
                  declaration=disposition)
    return request("delivery", packet)


def agree(tools, disposition, identifier="T1"):
    packet = delivery_packet(tools, disposition, identifier)
    try:
        result = tools.invoke(packet)
    except PoiseError as exc:
        if str(exc) == "Unknown work operation":
            pytest.fail("DELIVERY-AGREEMENT-MISSING: explicit destination agreement is unavailable")
        raise
    assert result["status"] == "delivery_agreed"
    return packet


def file_output(source, destination, kind="file"):
    return {
        "id": "customer-file", "kind": kind, "source": str(source),
        "destination": str(destination), "digest": hashlib.sha256(PAYLOAD).hexdigest(),
    }


def declaration(outputs):
    return {"disposition": "deliver", "outputs": outputs}


def settle(tools, identifier="T1", request_id="settle-materials"):
    return tools.invoke(request("delivery", {
        "action": "settle", "request_id": request_id, "task_id": identifier,
    }))


def fresh(project):
    return WorkTools(WorkPoise(project["config_path"], "material-resumer"))


def cancel_and_discard(tools, commit, identifier="T1"):
    result = tools.invoke(request("cancel", {"reason": "Cancel the exact fixture Task."}))
    assert result["status"] == "cancelled"
    return tools.invoke(request("cleanup", {
        "task_id": identifier, "request_id": "explicit-resource-disposition",
        "authorization": "The fixture customer explicitly discards this exact unique commit.",
        "commit_disposition": {"kind": "discard_authorized", "expected_commit": commit},
    }))


def finish(project, tools, commit, status):
    if status == "cancelled":
        return cancel_and_discard(tools, commit)
    from result_integration.helpers import integration_input
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    return tools.invoke(request("integrate", integration_input(project, commit)))


def absent(path):
    assert not os.path.lexists(path), "TERMINAL-FOLDER-RETAINED: owned working material survives"


def cli(project, packet):
    source = Path(__file__).resolve().parents[2] / "src"
    env = {k: v for k, v in os.environ.items()
           if k not in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "POISE_CALLER_BINDING")}
    env.update(POISE_CONFIG=str(project["config_path"]), PYTHONPATH=str(source),
               POISE_CALLER_BINDING=str(project["root"] / "material-cli.json"))
    output = subprocess.run([sys.executable, "-B", "-m", "poise", "work"],
                            input=json.dumps(packet), text=True, capture_output=True,
                            env=env, cwd=project["app"])
    response = json.loads(output.stdout)
    if "response_path" in response:
        response = json.loads(Path(response["response_path"]).read_text())
    return output.returncode, response


def delivery_status(tools, identifier="T1"):
    """Proposed read-only owning projection; never retries a failed effect."""
    response = tools.invoke(request("show", {"queries": [
        {"id": "delivery", "kind": "task_delivery", "task_id": identifier}]}))
    return response["results"][0]["value"]
