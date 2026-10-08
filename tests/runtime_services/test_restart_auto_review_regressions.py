"""Public regressions for current proof obligations and resumed Git safety."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from runtime_services.restart_auto_support import bootstrap, launch, prepared, snapshot
from runtime_services.test_task_restart import restart


@pytest.mark.parametrize("revision", ["expectation", "schedule", "evidence", "unchanged"])
def test_replay_preserves_the_admitted_proof_contract(project, revision):
    case = prepared(project)
    client = case["client"]
    current = client.runtime.task_queries.record("T1")
    born = restart(client, "T1", current["version"], request_id="revise-proof",
        authorization="User authorizes this disposable fixture's proof-contract revision.")
    contract = deepcopy(current["contract"])
    if revision == "expectation":
        contract["methods"][0]["stderr_contains"] = ["independent-new-expectation-not-printed"]
        patch = {"methods": contract["methods"]}
    elif revision == "schedule":
        added = deepcopy(contract["methods"][0])
        added["id"] = "NEW"
        added["argv"].append("-f")
        added["stderr_contains"] = ["independent-added-check-not-printed"]
        added["verification_plan"]["green_stages"] = ["tests"]
        contract["methods"].append(added)
        contract["checks"]["tests"].append("NEW")
        inputs = deepcopy(project["task"]["method_inputs"])
        new_input = deepcopy(inputs[0])
        new_input["method_id"] = "NEW"
        inputs.append(new_input)
        patch = {"methods": contract["methods"], "checks": contract["checks"],
                 "method_inputs": inputs}
    elif revision == "evidence":
        contract["evidence_plan"]["tests"]["arguments"].append({
            "id": "new-proof", "kind": "logical", "phase": "prepare",
            "observation_methods": [],
        })
        patch = {"evidence_plan": contract["evidence_plan"]}
    else:
        patch = {"methods": contract["methods"]}
    edited = client.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "edit-proof",
        "expected_revision": born["revision"], "patch": patch, "remove": [],
    }))
    ready = client.invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": "ready-proof",
        "expected_revision": edited["revision"],
    }))
    assert ready["ready"] is True
    bootstrap(client, {"id": "T1"})
    admitted = deepcopy(client.runtime.task_queries.record("T1")["contract"])
    for field, value in patch.items():
        assert admitted[field] == value
    response = launch(case, target="code_review", request_id="replay-proof")
    assert client.runtime.task_queries.record("T1")["contract"] == admitted
    if revision == "unchanged":
        assert response["status"] == "progression_target_reached"
        assert case["log"].read_text().splitlines() == case["commits"]
    else:
        assert response["status"] == "progression_stopped"
        assert response["replay"]["reason"] == "proof_contract_changed"
        assert response["replay"]["passed"] == []
        assert response["stage"] == "tests"
        assert case["log"].read_text() == ""


@pytest.mark.parametrize("collision", [True, False])
def test_resumed_checkout_preserves_new_ignored_work(project, monkeypatch, collision):
    case = prepared(project)
    root = case["root"]
    git(root, "rm", "tests/test_double.py")
    git(root, "commit", "-m", "Preserve deleted test before replay")
    start = git(root, "rev-parse", "HEAD")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    (common / "info/exclude").write_text("tests/test_double.py\n")
    runtime = case["client"].runtime
    original = runtime._git
    interrupted = []

    def pause(cwd, *args, **kwargs):
        if Path(cwd) == root and args[:2] == ("reset", "--hard") and not interrupted:
            interrupted.append(True)
            raise PoiseError("test-owned interruption before reset")
        return original(cwd, *args, **kwargs)

    packet = request("advance", {
        "request_id": "resume-proof", "task_id": "T1", "target_stage": "code_review",
    })
    monkeypatch.setattr(runtime, "_git", pause)
    with pytest.raises(PoiseError, match="test-owned interruption"):
        case["client"].invoke(packet)
    monkeypatch.setattr(runtime, "_git", original)
    assert interrupted == [True]
    assert git(root, "rev-parse", "HEAD") == start
    recovery = git(root, "for-each-ref", "--format=%(refname)", "refs/poise/replay/")
    assert git(root, "rev-parse", recovery) == start
    foreign = root / "tests/test_double.py"
    if collision:
        foreign.write_bytes(b"independent ignored work created after interruption\n")
        before = snapshot(case)
        with pytest.raises(PoiseError, match="Ignored files collide"):
            case["client"].invoke(packet)
        assert snapshot(case) == before
        assert foreign.read_bytes() == b"independent ignored work created after interruption\n"
    else:
        response = case["client"].invoke(packet)
        assert response["status"] == "progression_target_reached"
        assert case["log"].read_text().splitlines() == case["commits"]
    assert git(root, "rev-parse", recovery) == start
