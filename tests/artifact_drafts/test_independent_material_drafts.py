"""Snapshot publication and draft CAS preserve the already accepted file."""
from copy import deepcopy
import os
from pathlib import Path
import sqlite3

import pytest

from conftest import WorkPoise
from poise.application.work import WorkTools
from tests.batch.helpers import configure, request
from tests.batch.test_independent_material_operations import (
    assert_material_success, paused_overlap, publish, start_task,
)


@pytest.fixture
def draft_source(project):
    configure(project)
    owner, context = start_task(project, "T1", "draft-owner")
    created = publish(owner, "accepted draft\n", "acceptance.txt")
    owner.invoke(request("release", {
        "task_id": "T1", "request_id": "draft-source-release",
        "expected_version": owner.runtime.task_queries.record("T1")["_version"],
        "reason": "The independent reviewer receives this released draft.",
    }))
    reviewer = WorkTools(WorkPoise(project["config_path"], "draft-reviewer"))
    identifier = created["artifacts"][0]["id"]
    packet = request("artifact_drafts", {
        "action": "recover", "request_id": "recover-acceptance",
        "task_id": "T1",
        "expected_version": reviewer.runtime.task_queries.record("T1")["_version"],
        "artifacts": [{"id": identifier, "scope": "task", "path": "acceptance.txt"}],
        "reason": "Correct the premature immutable registration for independent review.",
        "authorization": "The fixture owner authorized recovery of this exact acceptance file.",
    })
    return reviewer, context, packet, identifier


def decision(tools, artifact_id, action):
    return request("artifact_drafts", {
        "action": action, "request_id": "draft-" + action, "task_id": "T1",
        "expected_version": tools.runtime.task_queries.record("T1")["_version"],
        "artifact_ids": [artifact_id], "reason": "Record the independent draft decision.",
        "authorization": "The fixture owner authorized this exact draft decision.",
    })


def test_losing_draft_request_preserves_accepted_snapshot(project, draft_source):
    first, context, packet, _ = draft_source
    second = WorkTools(WorkPoise(project["config_path"], "other-reviewer"))
    competing = deepcopy(packet)
    competing["input"]["request_id"] = "competing-recovery"
    # Observe the actual snapshot target, without duplicating the production
    # filename algorithm in an expected-value generator.
    root = Path(context["task_root"])

    def intercept(patch, pause):
        original = os.link

        def link(source, target, *args, **kwargs):
            if Path(target).suffix == ".snapshot" and Path(target).is_relative_to(root):
                pause()
            return original(source, target, *args, **kwargs)
        patch.setattr(os, "link", link)

    def winning_recovery():
        result = second.invoke(competing)
        snapshot = Path(result["snapshots"][0]["snapshot_path"])
        return result, snapshot.stat().st_ino

    a, b = paused_overlap(lambda: first.invoke(packet), winning_recovery, intercept)
    if b[0] == "error" and b[1] == "LockUnavailable":
        pytest.fail("DRAFT-WINNER-BLOCKED: draft I/O serializes a second valid request", pytrace=False)
    assert b[0] == "ok", b
    accepted, winner_inode = b[1]
    assert accepted["status"] == "artifact_drafts_recovered"
    assert a[0] == "error" and a[4] is True, a
    path = Path(accepted["snapshots"][0]["snapshot_path"])
    assert path.read_bytes() == b"accepted draft\n"
    assert path.stat().st_ino == winner_inode
    inode = path.stat().st_ino
    assert second.invoke(competing)["status"] == "artifact_drafts_recovered"
    assert path.stat().st_ino == inode
    with sqlite3.connect(first.runtime.state / first.runtime.paths["database"]) as db:
        assert db.execute("SELECT count(*) FROM artifacts WHERE path=? AND owner=?",
                          (str(path), "T1")).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM journal WHERE task_id=? AND event=?",
                          ("T1", "artifact_draft.recovered")).fetchone()[0] == 1


@pytest.mark.parametrize("action", ["recover", "review", "finalize"])
def test_independent_task_progresses_during_draft_snapshot_io(project, draft_source, action):
    reviewer, context, packet, identifier = draft_source
    if action != "recover":
        reviewer.invoke(packet)
        packet = decision(reviewer, identifier, "review")
    if action == "finalize":
        reviewer.invoke(packet)
        packet = decision(reviewer, identifier, "finalize")
    b, _ = start_task(project, "B", "draft-independent-B")
    root = Path(context["task_root"])

    def intercept(patch, pause):
        original = os.open

        def opened(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path).is_relative_to(root):
                pause()
            return original(path, *args, **kwargs)
        patch.setattr(os, "open", opened)

    left, right = paused_overlap(lambda: reviewer.invoke(packet), lambda: publish(b), intercept)
    assert_material_success(right)
    assert left[0] == "ok", left
    assert left[1]["status"] == {
        "recover": "artifact_drafts_recovered", "review": "artifact_drafts_reviewed",
        "finalize": "artifact_drafts_finalized",
    }[action]
