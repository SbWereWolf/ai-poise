"""Public cancellation semantics across standalone Tasks and Sprints."""
from copy import deepcopy
from pathlib import Path

import pytest

from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from conftest import WorkPoise as Poise
from batch.helpers import bootstrap as bootstrap_task, request, result, verify as verify_task
from conftest import add_test, write_json
from sprints.helpers import bootstrap, draft, publish, setup, task, verify


def change_limit(project):
    cfg = deepcopy(project["cfg"])
    cfg["limits"]["output_chars"] += 1
    project["cfg"] = cfg
    write_json(project["config_path"], cfg)


def sprint_plan(tools, sprint_id="S"):
    response = tools.invoke(request("show", {"queries": [{
        "id": "plan",
        "kind": "sprint",
        "sprint_id": sprint_id,
        "view": "plan",
    }]}))
    return response["results"][0]["value"]["aggregate"]


def test_standalone_cancel_ignores_config_digest_and_releases_claim(project):
    original = WorkTools(Poise(project["config_path"], "owner"))
    context = bootstrap_task(original, project)
    add_test(context["worktree"])
    verified = verify_task(original, result(context, "Keep this verified result"))
    before_counts = original.runtime.store.counts("T1")
    before_report = deepcopy(original.runtime.task_queries.record("T1")["last_report"])

    change_limit(project)
    current = WorkTools(Poise(project["config_path"], "owner"))
    cancelled = current.invoke(request("cancel", {"reason": "User cancels this Task"}))
    record = current.runtime.task_queries.record("T1")

    assert {
        key: cancelled[key] for key in ("status", "task", "worktree_preserved")
    } == {"status": "cancelled", "task": "T1", "worktree_preserved": True}
    assert cancelled["cleanup"]["status"] == "disposition_required"
    assert cancelled["cleanup"]["remaining_resources"]
    assert cancelled["interaction"]["outcome"] == "cancelled"
    assert record["status"] == "cancelled" and record["claimed_by"] is None
    assert record["last_report"] == before_report
    assert current.runtime.store.counts("T1") == before_counts
    assert Path(context["worktree"]).is_dir()
    assert verified["commit"] == record["last_report"]["commit"]
    assert any(
        event["event"] == "user_cancel" and event["reason"] == "User cancels this Task"
        for event in current.runtime.task_queries.history("T1")
    )


def test_selected_sprint_cancel_uses_task_domain_and_is_atomic(project):
    setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [task(project, "A"), task(project, "B")])
    publish(planner, planned["revision"])
    worker = WorkTools(Poise(project["config_path"], "worker-A"))
    context = bootstrap(worker, "A")
    marker = Path(context["worktree"]) / "unfinished.txt"
    marker.write_text("owned WIP\n")

    change_limit(project)
    current = WorkTools(Poise(project["config_path"], "planner"))
    overview = current.invoke(request("sprint", {
        "action": "cancel_tasks",
        "sprint_id": "S",
        "request_id": "cancel-selected",
        "tasks": ["A"],
        "mode": "single",
        "reason": "User removes A from this Sprint",
    }))

    assert overview["eligible"] == ["B"]
    assert overview["cleanup"]["A"]["status"] == "cleanup_blocked"
    assert overview["cleanup"]["A"]["blocker"]["reason"] == "dirty_worktree_requires_decision"
    assert overview["cleanup"]["A"]["blocker"]["resource"]["kind"] == "worktree"
    assert overview["cleanup"]["A"]["remaining_resources"]
    assert current.runtime.task_queries.record("A")["status"] == "cancelled"
    assert current.runtime.task_queries.record("A")["claimed_by"] is None
    assert current.runtime.task_queries.record("B")["status"] == "available"
    assert marker.read_text() == "owned WIP\n"
    assert any(
        event["event"] == "user_cancel" and event["reason"] == "User removes A from this Sprint"
        for event in current.runtime.task_queries.history("A")
    )
    assert sprint_plan(current)["decisions"][-1] == {
        "kind": "cancel_tasks",
        "tasks": ["A"],
        "reason": "User removes A from this Sprint",
    }


def test_selected_sprint_cancel_rejects_pending_batch_without_partial_effect(project):
    setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [task(project, "A"), task(project, "B")])
    publish(planner, planned["revision"])
    worker_a = WorkTools(Poise(project["config_path"], "worker-A"))
    bootstrap(worker_a, "A")
    worker_b = WorkTools(Poise(project["config_path"], "worker-B"))
    bootstrap(worker_b, "B")
    pending = worker_b.runtime.current_task()
    pending["pending"] = "checks"
    worker_b.runtime.store.save(pending)

    with pytest.raises(PoiseError, match="External operation outcome"):
        planner.invoke(request("sprint", {
            "action": "cancel_tasks",
            "sprint_id": "S",
            "request_id": "cancel-pending-batch",
            "tasks": ["A", "B"],
            "mode": "single",
            "reason": "User requests an atomic branch cancellation",
        }))

    assert planner.runtime.task_queries.record("A")["status"] == "active"
    assert planner.runtime.task_queries.record("A")["claimed_by"] == "worker-A"
    assert planner.runtime.task_queries.record("B")["status"] == "active"
    assert sprint_plan(planner)["decisions"] == []


def test_whole_sprint_cancel_preserves_completed_and_cancels_unfinished(project):
    setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [task(project, name) for name in "ABCD"])
    publish(planner, planned["revision"])

    worker_a = WorkTools(Poise(project["config_path"], "worker-A"))
    completed_context = bootstrap(worker_a, "A")
    completed_report = verify(worker_a, completed_context)
    worker_a.invoke(request("accept", {}))
    completed_record = deepcopy(planner.runtime.task_queries.record("A"))

    planner.invoke(request("sprint", {
        "action": "cancel_tasks",
        "sprint_id": "S",
        "request_id": "cancel-B-first",
        "tasks": ["B"],
        "mode": "single",
        "reason": "User already removed B",
    }))
    cancelled_b_history = deepcopy(planner.runtime.task_queries.history("B"))
    shared = planner.invoke(request("artifacts", {"items": [{
        "scope": "sprint",
        "path": "cancel-note.txt",
        "source": {"kind": "text", "text": "preserve this Sprint artifact\n"},
    }]}))
    shared_path = Path(shared["artifact_paths"][0])
    worker_c = WorkTools(Poise(project["config_path"], "worker-C"))
    active_context = bootstrap(worker_c, "C")
    marker = Path(active_context["worktree"]) / "unfinished.txt"
    marker.write_text("do not delete\n")
    unstarted_worktree = planner.runtime.state / planner.runtime.paths["worktrees"] / "D"
    assert not unstarted_worktree.exists()

    stored_hashes = {
        name: planner.runtime.task_queries.record(name)["config_hash"] for name in "ABCD"
    }
    change_limit(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    assert all(stored_hash != planner.runtime.config_hash for stored_hash in stored_hashes.values())

    overview = planner.invoke(request("sprint", {
        "action": "cancel",
        "sprint_id": "S",
        "request_id": "cancel-whole-sprint",
        "reason": "User cancels the entire Sprint",
    }))

    records = {name: planner.runtime.task_queries.record(name) for name in "ABCD"}
    assert overview["status"] == "cancelled"
    assert records["A"]["status"] == "completed"
    assert records["A"]["last_report"] == completed_record["last_report"]
    assert records["A"]["last_report"]["commit"] == completed_report["commit"]
    assert records["B"]["status"] == "cancelled"
    assert planner.runtime.task_queries.history("B") == cancelled_b_history
    assert records["C"]["status"] == records["D"]["status"] == "cancelled"
    assert records["C"]["claimed_by"] is None and records["D"]["claimed_by"] is None
    assert marker.read_text() == "do not delete\n"
    assert shared_path.read_text() == "preserve this Sprint artifact\n"
    assert not unstarted_worktree.exists()
    assert sprint_plan(planner)["decisions"][-1] == {
        "kind": "sprint_cancel",
        "tasks": ["C", "D"],
        "reason": "User cancels the entire Sprint",
    }
    for name in "CD":
        assert any(
            event["event"] == "user_cancel" and event["reason"] == "User cancels the entire Sprint"
            for event in planner.runtime.task_queries.history(name)
        )


def test_draft_sprint_cancel_does_not_publish_tasks(project):
    setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    drafted = draft(planner, [{"id": "A", "sprint_id": "S"}])
    assert drafted["errors"]

    overview = planner.invoke(request("sprint", {
        "action": "cancel",
        "sprint_id": "S",
        "request_id": "cancel-draft",
        "reason": "User cancels Sprint planning",
    }))
    assert overview["status"] == "cancelled"
    assert planner.runtime.task_queries.summary() == []
    assert not (planner.runtime.state / planner.runtime.paths["worktrees"]).exists()
    assert sprint_plan(planner)["decisions"][-1] == {
        "kind": "sprint_cancel",
        "tasks": [],
        "reason": "User cancels Sprint planning",
    }


def test_force_close_is_not_a_public_action(project):
    setup(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    draft(planner, [task(project, "A")])

    with pytest.raises(PoiseError, match="Unknown sprint action"):
        planner.invoke(request("sprint", {
            "action": "force_close",
            "sprint_id": "S",
            "request_id": "obsolete-action",
            "reason": "This obsolete API must be rejected",
        }))
    assert sprint_plan(planner)["state"] == "draft"
