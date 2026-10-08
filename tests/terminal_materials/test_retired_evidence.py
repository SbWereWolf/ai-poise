"""Actual executed output and registered non-deliverable material retirement."""
from pathlib import Path

import pytest

from batch.helpers import request, text_artifact
from poise.common import PoiseError
from .archive_oracle import no_new_archive, proof_cells
from .helpers import (absent, agree, arrange, cli, declaration, delivery_status,
                      file_output, finish)

MARKERS = (b"executed material proof 491b3d\n", b"executed material stderr 491b3d\n",
           b"declared material proof 491b3d\x00\xff\n", b"registered temporary proof 491b3d\n")


def checked(project):
    tools, root, source, commit = arrange(project, with_checks=True,
        artifact_files=[text_artifact("task", "temporary-proof.txt", "registered temporary proof 491b3d\n")])
    records = tools.runtime.evidence_commands.list_for("T1")
    assert len(records) == 1
    receipt = records[0]
    assert receipt["method"] == "CHECK" and receipt["actual_exit_code"] == 0 and receipt["passed"]
    assert b"executed material proof 491b3d\n" in Path(receipt["stdout"]).read_bytes()
    assert b"executed material stderr 491b3d\n" in Path(receipt["stderr"]).read_bytes()
    captured = receipt["outputs"][0]
    assert captured["status"] == "captured"
    assert Path(captured["path"]).read_bytes() == b"declared material proof 491b3d\x00\xff\n"
    artifact = tools.runtime.store.artifact_records("T1")[0]
    assert Path(artifact["path"]).read_bytes() == b"registered temporary proof 491b3d\n"
    return tools, root, source, commit, receipt, artifact


def test_real_proof_arrangement_executes_registered_method(project):
    # Separate GREEN fixture guard. It certifies real arrangement, not retirement.
    checked(project)


def test_archive_oracle_falsifies_a_new_sqlite_proof_copy(project):
    tools, _, _, _, _, _ = checked(project)
    before = proof_cells(tools, MARKERS)
    with tools.runtime.store.transaction() as database:
        database.execute("CREATE TABLE fixture_illegal_archive(payload BLOB NOT NULL)")
        database.execute("INSERT INTO fixture_illegal_archive(payload) VALUES (?)", (MARKERS[2],))
    assert set(proof_cells(tools, MARKERS)) - set(before) == {("fixture_illegal_archive", 1, "payload")}


def test_actual_proofs_and_registered_temporary_artifact_retire_without_new_archive(project):
    tools, root, source, commit, receipt, artifact = checked(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    before = proof_cells(tools, MARKERS)
    finish(project, tools, commit, "cancelled")
    absent(root)
    no_new_archive(tools, before, MARKERS)
    state = delivery_status(tools)
    assert state["status"] == "delivery_complete"
    assert artifact["id"] in state["retired_artifacts"]
    for path in (receipt["stdout"], receipt["stderr"], receipt["outputs"][0]["path"], artifact["path"]):
        assert not Path(path).exists()
    counts = tools.runtime.store.counts("T1")
    history = tools.runtime.task_queries.history("T1")
    code, terminal = cli(project, request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
    assert code == 0 and terminal["status"] == "cancelled" and terminal["result_template"] is None
    record = next(item for item in terminal["evidence"]["records"] if item["id"] == receipt["id"])
    for key in ("id", "method", "stage", "iteration", "actual_exit_code"):
        assert record[key] == receipt[key]
    assert record["availability"] == "retired"
    assert "historical" in terminal["notice"].lower()
    assert tools.runtime.store.counts("T1") == counts
    assert tools.runtime.task_queries.history("T1") == history
    assert destination.read_bytes() == b"customer result\n"
    absent(root)


def test_integration_replay_with_actual_removed_check_files_returns_only_history(project):
    from result_integration.helpers import integration_input
    tools, root, source, commit, receipt, _ = checked(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    packet = request("integrate", integration_input(project, commit))
    first = tools.invoke(packet)
    assert first["status"] == "integrated"
    absent(root)
    assert not Path(receipt["stdout"]).exists()
    counts = tools.runtime.store.counts("T1")
    code, replay = cli(project, packet)
    assert code == 0 and replay["status"] == "integrated" and replay["replayed"] is True
    assert replay["checks"] == first["checks"]
    assert tools.runtime.store.counts("T1") == counts
    absent(root)


def test_corrupt_active_output_view_remains_integrity_failure_before_retirement(project):
    tools, root, _, _, receipt, _ = checked(project)
    import json
    query = request("show", {"queries": [{"id": "proof", "kind": "tool_result",
        "receipt_id": receipt["id"], "representation": "full", "range": None}]})
    observed = tools.invoke(query)["results"][0]["value"]
    assert "executed material proof 491b3d" in observed["text"]
    manifest = Path(tools.runtime.task_queries.resolve_path("T1", receipt["presentation"]["manifest"]))
    view = json.loads(manifest.read_text())["representations"]["full"]
    path = Path(tools.runtime.task_queries.resolve_path("T1", view["path"]))
    path.write_bytes(b"corrupt active output view\n")
    before = tools.runtime.store.counts("T1")
    with pytest.raises(PoiseError, match="[Pp]resentation.*changed|digest|integrity"):
        tools.invoke(query)
    assert tools.runtime.task_queries.record("T1")["status"] == "verified"
    assert root.is_dir() and tools.runtime.store.counts("T1") == before
