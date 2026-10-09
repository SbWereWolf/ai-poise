"""Retired acceptance proof is history; unretired missing bytes remain invalid."""
from pathlib import Path
import shutil
import hashlib

import pytest

from batch.helpers import request
from result_integration.helpers import integration_input
from terminal_materials.helpers import agree, cli, declaration, file_output
from verification.retention_helpers import BYTES, verify_input


def test_completed_retained_integration_replays_and_queries_after_retirement(project, tmp_path):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    root = Path(context["task_root"])
    retained = root / "artifacts/inputs/accepted.whl"
    destination = project["root"] / "delivered.whl"
    output = file_output(retained, destination)
    output["digest"] = hashlib.sha256(BYTES).hexdigest()
    agree(tools, declaration([output]))
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    packet = request("integrate", integration_input(project, done["commit"]))
    first = tools.invoke(packet)
    assert first["status"] == "integrated"
    assert first["delivery"]["status"] == "delivery_complete"
    assert destination.read_bytes() == BYTES and not root.exists()
    counts = tools.runtime.store.counts("T1")
    executions = marker.read_text()

    code, replay = cli(project, packet)
    assert code == 0, replay
    assert replay["status"] == "integrated" and replay["replayed"] is True
    assert replay["checks"] == first["checks"]
    assert "acceptance_manifests" not in replay
    query = request("show", {"queries": [{
        "id": "integration", "kind": "integration", "task_id": "T1",
        "request_id": packet["input"]["request_id"],
    }]})
    code, observed = cli(project, query)
    assert code == 0, observed
    historical = observed["results"][0]["value"]
    assert historical["status"] == "integrated" and historical["checks"] == first["checks"]
    assert "acceptance_manifests" not in historical
    assert tools.runtime.store.counts("T1") == counts
    assert marker.read_text() == executions
    assert destination.read_bytes() == BYTES and not root.exists()
    assert not Path(context["worktree"]).exists()


@pytest.mark.parametrize("fault", ["root", "input"])
def test_completed_integration_refuses_unagreed_retention_loss(project, tmp_path, fault):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    packet = request("integrate", integration_input(project, done["commit"]))
    first = tools.invoke(packet)
    assert first["status"] == "integrated"
    assert tools.runtime.delivery_tools.query("T1")["status"] == "delivery_unagreed"
    root = Path(context["task_root"])
    if fault == "root":
        shutil.rmtree(root)
    else:
        (root / "artifacts/inputs/accepted.whl").unlink()
    counts = tools.runtime.store.counts("T1")
    executions = marker.read_text()
    code, refused = cli(project, packet)
    assert code == 2, refused
    assert "missing" in refused["reason"].lower()
    assert tools.runtime.store.counts("T1") == counts
    assert marker.read_text() == executions
    assert not (root / "artifacts/inputs/accepted.whl").exists()
    assert not Path(context["worktree"]).exists()
