"""Public recovery refuses absent authority and stale execution context."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_durable_check_attempts import candidate, fail_once
from runtime_services.test_restart_ownership_attempt import uncertain
from runtime_services.test_task_restart import restart


CONTRACT = json.loads((Path(__file__).parent / "fixtures" /
                       "restart_public_guards_contract.json").read_text())


def _snapshot(project, tools, actors):
    """Observe disposable domain rows independently of production serializers."""
    database = (project["root"] / project["cfg"]["paths"]["state"] /
                project["cfg"]["paths"]["database"])
    marks = ",".join("?" for _ in actors)
    queries = {
        "tasks": (
            "SELECT id,status,stage_index,iteration,claimed_by,version,"
            "current_submission_id,metadata FROM tasks WHERE id=?",
            ("T1",),
        ),
        "execution": (
            "SELECT task_id,data,version FROM task_execution WHERE task_id=?",
            ("T1",),
        ),
        "workflow": (
            "SELECT task_id,data FROM task_workflows WHERE task_id=?",
            ("T1",),
        ),
        "events": (
            "SELECT seq,task_id,version,at,data FROM task_events "
            "WHERE task_id=? ORDER BY seq",
            ("T1",),
        ),
        "claims": (
            f"SELECT id,task_id FROM sessions WHERE id IN ({marks}) "
            "OR task_id=? ORDER BY id",
            (*actors, "T1"),
        ),
    }
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.row_factory = sqlite3.Row
        rows = {
            name: tuple(dict(row) for row in connection.execute(sql, parameters))
            for name, (sql, parameters) in queries.items()
        }
    receipts = deepcopy(tools.runtime.evidence_commands.list_for("T1"))
    output_digests = {}
    for receipt in receipts:
        for stream in ("stdout", "stderr"):
            digest = hashlib.sha256(Path(receipt[stream]).read_bytes()).hexdigest()
            assert digest == receipt[stream + "_digest"]
            output_digests[(receipt["id"], stream)] = digest
    workspace = Path(tools.runtime.task_queries.record("T1")["worktree"])

    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(workspace), *arguments], text=True,
        )

    return {
        "rows": rows,
        "receipts": receipts,
        "output_digests": output_digests,
        "head": git("rev-parse", "HEAD"),
        "status": git("status", "--porcelain"),
    }


def _acquire(tools):
    acquired = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert acquired["status"] == "active"


@pytest.mark.parametrize("case", CONTRACT["invalid_authorities"],
                         ids=lambda case: case["id"])
def test_restart_without_authority_preserves_original_attempt(project, monkeypatch, case):
    producer, receiver, original, calls = uncertain(project, monkeypatch)
    attempt = original["pending"]
    assert attempt["kind"] == "check_attempt"
    assert attempt["version"] == 2
    proof = attempt["runs"][0]["termination"]
    for field in ("child_reaped", "process_group_stopped", "capture_complete"):
        assert proof[field] is True
    actors = (producer.runtime.session, receiver.runtime.session)
    before = _snapshot(project, receiver, actors)
    task = before["rows"]["tasks"][0]
    assert task["claimed_by"] == receiver.runtime.session
    assert task["version"] > attempt["task_version"]
    assert json.loads(before["rows"]["execution"][0]["data"])["pending"] == attempt

    with pytest.raises(PoiseError, match="authorization|role|decision"):
        restart(receiver, "T1", task["version"], "invalid-authority-" + case["id"],
                authorization=deepcopy(case["authorization"]))

    assert _snapshot(project, receiver, actors) == before
    assert calls == [attempt["runs"][0]["run_id"]]


@pytest.mark.parametrize("context", CONTRACT["replay_contexts"], ids=str)
def test_exact_verify_obeys_current_owner_and_version(project, monkeypatch, context):
    producer, payload, calls = candidate(project, monkeypatch)
    packet = request("verify", {"result": deepcopy(payload), "artifacts": []})
    fail_once(monkeypatch, producer.runtime.runner, "record_observations")
    with pytest.raises(OSError, match="injected persistence failure"):
        producer.invoke(deepcopy(packet))
    attempt = deepcopy(producer.runtime.current_task()["pending"])
    assert attempt["kind"] == "check_attempt"
    receipt, = producer.runtime.evidence_commands.list_for("T1")
    assert receipt["id"] == attempt["runs"][0]["run_id"]
    assert receipt["passed"] is True
    assert receipt["capture_complete"] is True
    assert calls == [receipt["id"]]

    receiver = producer
    if context != "unchanged-control":
        producer.runtime.ownership.release_task("T1")
        if context == "distinct-owner-release-and-acquire":
            receiver = WorkTools(WorkPoise(project["config_path"], "public-replay-receiver"))
        _acquire(receiver)

    actors = tuple(sorted({producer.runtime.session, receiver.runtime.session}))
    before = _snapshot(project, receiver, actors)
    task = before["rows"]["tasks"][0]
    assert task["claimed_by"] == receiver.runtime.session
    if context == "unchanged-control":
        assert task["version"] == attempt["task_version"]
        replay = receiver.invoke(deepcopy(packet))
        assert replay["status"] == "awaiting_continuation"
        assert producer.runtime.current_task()["pending"] is None
        assert producer.runtime.current_task()["attempts"] == 1
    else:
        assert task["version"] > attempt["task_version"]
        if context == "same-owner-release-and-reacquire":
            assert task["claimed_by"] == attempt["actor"]
        else:
            assert task["claimed_by"] != attempt["actor"]
        with pytest.raises(PoiseError):
            receiver.invoke(deepcopy(packet))
        assert _snapshot(project, receiver, actors) == before
    assert calls == [receipt["id"]]
