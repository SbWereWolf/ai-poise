"""The direct data-publication consumer shares the current lifecycle selector."""
from copy import deepcopy
import json
import sqlite3

import pytest

from conftest import WorkPoise
from catalogue.test_publication import call, hand_over, setup, start, verify
from poise.modules.foundation.errors import PoiseError
from .lifecycle_helpers import FIXTURES, rows


def child_states(client):
    with client.store.transaction() as database:
        return [tuple(row) for row in database.execute(
            "SELECT id,status FROM tasks WHERE id IN (?,?,?) ORDER BY id",
            ("CHILD", "CHILD-CURRENT", "CHILD-ROLLBACK"),
        )]


def reviewed_publication(client, context, children, path, cycle):
    # Runtime-dependent fixture contracts remain file-backed test input.
    path.write_text(json.dumps(children, ensure_ascii=False, indent=2) + "\n")
    verify(client, context, {}, {"planned_tasks": path.read_text()})
    reviewer = WorkPoise(client.config_path, "CHILD-REVIEWER-" + cycle)
    hand_over(client, reviewer, context["task"], "children-to-review-" + cycle)
    inspected = start(reviewer, decision="continue")
    verify(reviewer, inspected, {
        "coverage": "Inspect exact fixture child contracts.",
        "findings": [], "resolution_decisions": [],
    }, {"report": "Reviewed test-owned child contracts."})
    call(reviewer, "accept", {})
    hand_over(reviewer, client, context["task"], "children-to-publish-" + cycle)
    return start(client, decision="continue")


def restart_parent(client, context):
    reviewer = WorkPoise(client.config_path, "CHILD-RESTART-REVIEWER")
    acquired = hand_over(client, reviewer, context["task"], "children-to-restart")
    draft = call(reviewer, "task", {
        "action": "restart", "request_id": "children-restart", "task_id": context["task"],
        "expected_version": acquired["version"], "reason": "Review a new child batch in this Task.",
        "authorization": "Independent fixture reviewer authorizes this unfinished Task restart.",
    })
    ready = call(reviewer, "task", {
        "action": "ready", "request_id": "children-ready", "task_id": context["task"],
        "expected_revision": draft["revision"],
    })
    assert ready["status"] == "available"
    resumed = start(client, {"id": context["task"]})
    assert resumed["task"] == context["task"]
    assert resumed["worktree"] == context["worktree"]
    assert resumed["stage"] == "draft"
    return resumed


@pytest.mark.parametrize("restarted", [False, True], ids=["initial", "restarted"])
def test_reviewed_child_batch_uses_current_scope_and_preserves_atomicity(project, tmp_path, restarted):
    client, context, old_child = setup(project)
    intent = json.loads((FIXTURES / "lifecycle_publication_intent.json").read_text())
    if restarted:
        context = reviewed_publication(client, context, [old_child], tmp_path / "prior-children.json", "prior")
        assert verify(client, context, intent, {})["action"]["status"] == "complete"
        assert child_states(client) == [("CHILD", "available")]
        context = restart_parent(client, context)
    first, second = deepcopy(old_child), deepcopy(old_child)
    first["id"], second["id"] = "CHILD-CURRENT", "CHILD-ROLLBACK"
    context = reviewed_publication(client, context, [first, second], tmp_path / "current-children.json", "current")
    before = rows(client, context["task"])
    with client.store.transaction() as database:
        database.execute("CREATE TRIGGER reject_lifecycle_child BEFORE INSERT ON tasks "
                         "WHEN NEW.id='CHILD-ROLLBACK' BEGIN SELECT RAISE(ABORT,'injected lifecycle child failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="^injected lifecycle child failure$"):
            verify(client, context, intent, {})
    except PoiseError as error:
        if str(error) == "Reviewed publication intent changed":
            pytest.fail("LIFECYCLE_CHILD_PUBLICATION_SELECTED_HISTORY")
        raise
    expected_prior = [("CHILD", "available")] if restarted else []
    assert child_states(client) == expected_prior
    after_failure = rows(client, context["task"])
    assert after_failure["runs"] == before["runs"]
    assert after_failure["events"] == before["events"]
    with client.store.transaction() as database:
        database.execute("DROP TRIGGER reject_lifecycle_child")
    completed = verify(client, context, intent, {})
    assert completed["action"]["status"] == "complete"
    assert completed["action"]["plan"]["kind"] == "data_publish"
    assert completed["action"]["plan"]["intent"] == intent
    assert completed["action"]["plan"]["body"] == [first, second]
    assert child_states(client) == expected_prior + [
        ("CHILD-CURRENT", "available"), ("CHILD-ROLLBACK", "available"),
    ]
    after = rows(client, context["task"])
    assert all(row in after["runs"] for row in before["runs"])
    assert after["events"][:len(before["events"])] == before["events"]
    assert len(after["runs"]) == (2 if restarted else 1)
    replay = call(client, "verify", {"result": None, "artifacts": []})
    assert replay["replayed"] is True
    assert replay["action"]["plan"] == completed["action"]["plan"]
    assert rows(client, context["task"]) == after
