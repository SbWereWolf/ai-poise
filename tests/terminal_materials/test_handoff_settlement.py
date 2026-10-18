"""Current handoff obligations at the public terminal delivery boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request
from .helpers import (absent, agree, arrange, declaration, delivery_status,
                      file_output, finish, fresh, settle)


def release(tools, request_id):
    saved = tools.invoke(request("handoff", {
        "request_id": request_id,
        "reason": "Preserve verified work for the next owner.",
        "result": None, "commit_message": None, "artifact_paths": [],
    }))
    assert saved["status"] == "handed_off"


def handoff_rows(tools):
    with tools.runtime.store.database.read_transaction() as database:
        return [tuple(row) for row in database.execute(
            "SELECT seq, actor, request_id, task_id, state, data "
            "FROM handoffs WHERE task_id = ? ORDER BY seq", ("T1",)
        ).fetchall()]


def assert_no_current_handoff(tools):
    candidate = tools.runtime.handoff_tools.commands.latest("T1")
    if candidate is not None:
        assert candidate["task_id"] == "T1"
        assert candidate["request_id"] == "old-release"
        assert candidate["state"] == "released"
        assert [(row[2], row[4]) for row in handoff_rows(tools)] == [
            ("old-release", "released"), ("current-release", "resumed"),
        ]
        pytest.fail(
            "RR-TARGET-CURRENT-HANDOFF-SELECTION: resumed current transfer revives an older release",
            pytrace=False,
        )
    assert candidate is None


def released_chain(project):
    project["cfg"]["runtime_services"]["handoff"] = json.loads(
        (Path(__file__).parents[1] / "sprints" / "fixtures" / "file-handoff.json").read_text())
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "delivered-result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    release(tools, "old-release")
    # The real ownership owner preserves history when reacquiring released work.
    tools.runtime.ownership.acquire_task("T1")
    release(tools, "current-release")
    return tools, root, source, commit, destination


@pytest.mark.parametrize("foreign_pending", [False, True], ids=["alone", "foreign-pending"])
def test_resumed_handoff_allows_public_terminal_delivery(project, foreign_pending):
    tools, root, _, commit, destination = released_chain(project)
    receiver = fresh(project)
    resumed = receiver.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert resumed["status"] == "verified"
    receipts = handoff_rows(receiver)
    assert [(row[2], row[4]) for row in receipts] == [
        ("old-release", "released"), ("current-release", "resumed"),
    ]
    assert_no_current_handoff(receiver)

    if foreign_pending:
        foreign, foreign_root, foreign_source, _ = arrange(
            project, identifier="FOREIGN-CATALOG-EXPORT", session="catalog-export-owner")
        assert foreign.runtime.current_task()["goal"] == "Результат FOREIGN-CATALOG-EXPORT"
        release(foreign, "catalog-export-pending")
        foreign_record = deepcopy(foreign.runtime.task_queries.record("FOREIGN-CATALOG-EXPORT"))
        foreign_history = foreign.runtime.task_queries.history("FOREIGN-CATALOG-EXPORT")

    history = receiver.runtime.task_queries.history("T1")
    integrated = finish(project, receiver, commit, "completed")

    assert integrated["status"] == "integrated"
    assert delivery_status(fresh(project))["status"] == "delivery_complete"
    absent(root)
    assert destination.read_bytes() == b"customer result\n"
    assert handoff_rows(receiver) == receipts
    assert receiver.runtime.task_queries.history("T1")[:len(history)] == history
    assert_no_current_handoff(receiver)
    if foreign_pending:
        assert foreign.runtime.task_queries.record("FOREIGN-CATALOG-EXPORT") == foreign_record
        assert foreign.runtime.task_queries.history("FOREIGN-CATALOG-EXPORT") == foreign_history
        assert foreign_root.is_dir()
        assert foreign_source.read_bytes() == b"customer result\n"


def test_current_released_handoff_retains_materials(project):
    tools, root, source, _, destination = released_chain(project)
    receipts = handoff_rows(tools)
    assert [(row[2], row[4]) for row in receipts] == [
        ("old-release", "released"), ("current-release", "released"),
    ]
    record = deepcopy(tools.runtime.task_queries.record("T1"))
    history = tools.runtime.task_queries.history("T1")

    blocked = settle(fresh(project))

    assert blocked["status"] == "delivery_blocked"
    assert blocked["reason"] == "handoff_pending"
    assert root.is_dir()
    assert source.read_bytes() == b"customer result\n"
    assert not destination.exists()
    assert handoff_rows(tools) == receipts
    assert tools.runtime.task_queries.record("T1") == record
    assert tools.runtime.task_queries.history("T1")[:len(history)] == history
    assert tools.runtime.handoff_tools.commands.latest("T1")["request_id"] == "current-release"
