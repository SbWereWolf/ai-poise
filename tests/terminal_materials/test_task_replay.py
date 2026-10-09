"""Preserved native Task action/progression identities after whole-folder retirement."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from sprints.helpers import setup, task
from .helpers import absent, agree, cancel_and_discard, file_output, declaration


@pytest.mark.parametrize("action", ["create", "edit", "ready", "restart", "advance"])
def test_completed_original_action_replays_without_task_folder(project, action):
    setup(project)
    tools = WorkTools(WorkPoise(project["config_path"], "action-owner"))
    packets = {}
    packets["create"] = request("task", {
        "action": "create", "request_id": "create-original", "task_id": "T1", "sprint_id": None,
    })
    born = tools.invoke(packets["create"])
    contract = task(project, "T1")
    contract["sprint_id"] = None
    contract["methods"], contract["method_inputs"], contract["checks"] = [], [], {"work": []}
    for key in ("id", "sprint_id"):
        contract.pop(key)
    packets["edit"] = request("task", {
        "action": "edit", "request_id": "edit-original", "task_id": "T1",
        "expected_revision": born["revision"], "patch": contract, "remove": [],
    })
    edited = tools.invoke(packets["edit"])
    packets["ready"] = request("task", {
        "action": "ready", "request_id": "ready-original", "task_id": "T1",
        "expected_revision": edited["revision"],
    })
    tools.invoke(packets["ready"])
    acquire = request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    })
    context = tools.invoke(acquire)
    if action == "restart":
        packets["restart"] = request("task", {
            "action": "restart", "request_id": "restart-original", "task_id": "T1",
            "expected_version": context["version"], "reason": "Restart this unfinished fixture before producing a result.",
            "authorization": "The independent fixture reviewer authorizes this exact restart.",
        })
        restarted = tools.invoke(packets["restart"])
        tools.invoke(request("task", {
            "action": "ready", "request_id": "ready-after-restart", "task_id": "T1",
            "expected_revision": restarted["revision"],
        }))
        context = tools.invoke(acquire)
    original_advance = None
    if action == "advance":
        packets["advance"] = request("advance", {
            "task_id": "T1", "request_id": "advance-original", "target_stage": "work"})
        original_advance = tools.invoke(packets["advance"])
        assert original_advance["status"] == "progression_target_reached"
        assert original_advance["replay"]["target_stage"] == "work"
        assert original_advance["replay"]["reason"] == "target_reached"
        with tools.runtime.store.transaction() as database:
            completed = [row[0] for row in database.execute(
                "SELECT data FROM journal WHERE task_id=? AND event=? ORDER BY seq",
                ("T1", "progression.noop"))]
        import json
        assert len(completed) == 1
        native_receipt = json.loads(completed[0])
        assert native_receipt["request_id"] == "advance-original"
        assert native_receipt["result"]["status"] == "progression_target_reached"
        assert native_receipt["result"]["replay"] == original_advance["replay"]
    root = Path(context["task_root"])
    (Path(context["worktree"]) / "src" / "action_result.py").write_text("VALUE = 7\n")
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "The exact action result is ready."
    result["commit_message"] = "Produce the exact action result"
    verified = tools.invoke(request("verify", {"result": result, "artifacts": []}))
    assert verified["status"] == "verified"
    source = root / "result.txt"
    source.write_bytes(b"customer result\n")
    destination = project["root"] / "delivered.txt"
    agree(tools, declaration([file_output(source, destination)]))
    cancel_and_discard(tools, verified["commit"])
    absent(root)
    history = tools.runtime.task_queries.history("T1")
    replay = tools.invoke(packets[action])
    assert replay["replayed"] is True
    if action == "advance":
        assert original_advance is not None
        for field in ("status", "task", "stage", "replay"):
            assert replay[field] == original_advance[field]
    assert tools.runtime.current_task() is None
    assert tools.runtime.task_queries.record("T1")["status"] == "cancelled"
    assert tools.runtime.task_queries.history("T1") == history
    assert destination.read_bytes() == b"customer result\n"
    absent(root)
