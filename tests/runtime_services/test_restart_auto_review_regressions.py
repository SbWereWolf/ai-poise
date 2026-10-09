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
        assert case["log"].read_text().splitlines() == [case["saved"]] * len(case["commits"])
    else:
        assert response["status"] == "progression_stopped"
        assert response["replay"]["reason"] == "proof_contract_changed"
        assert response["replay"]["passed"] == []
        assert response["stage"] == "tests"
        assert case["log"].read_text() == ""


@pytest.mark.parametrize("collision", [True, False])
def test_current_source_recheck_preserves_unrelated_ignored_work(project, monkeypatch, collision):
    case = prepared(project)
    root = case["root"]
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    (common / "info/exclude").write_text("tests/ignored-note\n")
    foreign = root / "tests/ignored-note"
    if collision:
        foreign.write_bytes(b"Independent ignored work\n")
    runtime = case["client"].runtime
    original = runtime._git
    destructive = []
    def observe(cwd, *args, **kwargs):
        if Path(cwd) == root and args and args[0] in ("reset", "checkout", "switch"):
            destructive.append(args)
        return original(cwd, *args, **kwargs)
    monkeypatch.setattr(runtime, "_git", observe)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert destructive == []
    assert git(root, "rev-parse", "HEAD") == case["saved"]
    assert case["log"].read_text().splitlines() == [case["saved"]] * 3
    if collision:
        assert foreign.read_bytes() == b"Independent ignored work\n"
    else:
        assert not foreign.exists()
