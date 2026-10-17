"""Interrupted ordinary cancellation does not require missing execution layers."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from ownership.test_worktree_free_proof import _prepare, _start
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_draft_continuation import draft


ABSENT_LAYERS = [
    (), ("pending",), ("contract",), ("execution",),
    ("pending", "contract"), ("pending", "execution"),
    ("contract", "execution"), ("pending", "contract", "execution"),
]


def foreign_rows(tools):
    with tools.runtime.store.transaction() as db:
        return {
            "task": [tuple(row) for row in db.execute("SELECT * FROM tasks WHERE id='KEEP'")],
            "session": [tuple(row) for row in db.execute("SELECT * FROM sessions WHERE id='foreign-owner'")],
            "execution": [tuple(row) for row in db.execute("SELECT * FROM task_execution WHERE task_id='KEEP'")],
            "methods": [tuple(row) for row in db.execute("SELECT * FROM task_methods WHERE task_id='KEEP' ORDER BY rowid")],
            "artifacts": [tuple(row) for row in db.execute("SELECT * FROM artifacts WHERE owner='KEEP' ORDER BY rowid")],
        }


def create_unrelated_task(project, root):
    foreign = WorkTools(WorkPoise(project["config_path"], "foreign-owner"))
    foreign.invoke(request("task", {
        "action": "create", "request_id": "create-foreign", "task_id": "KEEP", "sprint_id": None,
    }))
    keep = root.parent / "KEEP" / "keep.txt"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"unrelated working material\n")
    return foreign, keep


@pytest.mark.parametrize("absent", ABSENT_LAYERS)
def test_cancelled_cleanup_retries_with_independent_missing_layers(project, monkeypatch, absent):
    tools, task = _prepare(project)
    context = _start(tools, task)
    assert context["worktree"] is None
    root = Path(context["task_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "discarded-plan.txt").write_bytes(b"unfinished internal plan\n")
    repository_head = git(project["app"], "rev-parse", "HEAD")
    _, keep = create_unrelated_task(project, root)
    unrelated = foreign_rows(tools)

    def interrupt_removal(state):
        raise OSError("injected interruption before owned root removal")

    with monkeypatch.context() as interruption:
        interruption.setattr(tools.runtime.delivery_effects, "remove_owned_root", interrupt_removal)
        with pytest.raises(OSError, match="^injected interruption before owned root removal$"):
            tools.invoke(request("cancel", {"reason": "Discard the Task with no ordered result."}))

    record = tools.runtime.task_queries.record(task["id"])
    assert record["status"] == "cancelled" and record["claimed_by"] is None
    assert (root / "discarded-plan.txt").read_bytes() == b"unfinished internal plan\n"
    cleanup_intent = deepcopy(record["pending"]["intent"])
    cleanup_intent["commit_disposition"] = None
    # These writes affect only the disposable test database, after real cancellation.
    # No allocated worktree or branch is lost: this fixture explicitly requires neither.
    with tools.runtime.store.transaction() as db:
        if "contract" in absent:
            db.execute("UPDATE tasks SET metadata=json_remove(metadata,'$.contract') WHERE id=?", (task["id"],))
        if "pending" in absent:
            db.execute("UPDATE task_execution SET data=json_remove(data,'$.pending') WHERE task_id=?", (task["id"],))
        if "execution" in absent:
            db.execute("DELETE FROM task_execution WHERE task_id=?", (task["id"],))
        original_task = tuple(db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        original_history = [tuple(row) for row in db.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY seq", (task["id"],))]
        original_methods = [tuple(row) for row in db.execute(
            "SELECT * FROM task_methods WHERE task_id=? ORDER BY rowid", (task["id"],))]
        if "contract" in absent:
            assert "contract" not in json.loads(db.execute(
                "SELECT metadata FROM tasks WHERE id=?", (task["id"],)).fetchone()[0])
        if "execution" in absent:
            assert db.execute("SELECT COUNT(*) FROM task_execution WHERE task_id=?", (task["id"],)).fetchone()[0] == 0
        elif "pending" in absent:
            assert "pending" not in json.loads(db.execute(
                "SELECT data FROM task_execution WHERE task_id=?", (task["id"],)).fetchone()[0])

    packet = request("cleanup", cleanup_intent)
    completed = tools.invoke(packet)

    assert completed["status"] == "cleanup_complete"
    assert completed["remaining_resources"] == []
    assert not root.exists()
    assert keep.read_bytes() == b"unrelated working material\n"
    assert foreign_rows(tools) == unrelated
    assert git(project["app"], "rev-parse", "HEAD") == repository_head
    with tools.runtime.store.transaction() as db:
        assert tuple(db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()) == original_task
        assert [tuple(row) for row in db.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY seq", (task["id"],))] == original_history
        assert [tuple(row) for row in db.execute(
            "SELECT * FROM task_methods WHERE task_id=? ORDER BY rowid", (task["id"],))] == original_methods
    assert tools.invoke(packet)["status"] == "cleanup_complete"
    different_request = deepcopy(packet)
    different_request["input"]["request_id"] = "different-cleanup-request"
    with pytest.raises(PoiseError, match="^Cleanup request immutable intent changed$"):
        tools.invoke(different_request)
    assert not root.exists()
    assert foreign_rows(tools) == unrelated


def test_cancelled_no_result_task_releases_allocated_worktree_branch_and_service_material(project):
    tools, task = _prepare(project, worktree_required=True)
    context = _start(tools, task)
    root = Path(context["task_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "internal-plan.txt").write_bytes(b"discarded planning material\n")
    worktree = Path(context["worktree"])
    branch = tools.runtime.current_task()["branch"]
    (worktree / "scratch.note").write_bytes(b"unrequested unfinished material\n")
    foreign, keep = create_unrelated_task(project, root)
    unrelated = foreign_rows(tools)
    adapter = tools.runtime.cleanup_tools.adapter
    service_root = adapter.resource_root(task["id"])
    for kind in ("temporary", "temporary_backup"):
        resource = adapter.resource_root(task["id"], kind) / "internal.txt"
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(b"unrequested service material\n")
        adapter.register(task["id"], kind, resource)
    other_adapter = foreign.runtime.cleanup_tools.adapter
    other_resource = other_adapter.resource_root("KEEP", "temporary") / "keep.txt"
    other_resource.parent.mkdir(parents=True, exist_ok=True)
    other_resource.write_bytes(b"foreign service material\n")
    other_adapter.register("KEEP", "temporary", other_resource)
    assert git(project["app"], "show-ref", "--verify", "refs/heads/" + branch)

    cancelled = tools.invoke(request("cancel", {"reason": "Discard this Task and its unrequested work."}))

    assert cancelled["status"] == "cancelled"
    assert not root.exists()
    assert not worktree.exists()
    assert not service_root.exists()
    assert branch not in git(project["app"], "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    assert str(worktree) not in git(project["app"], "worktree", "list", "--porcelain")
    assert keep.read_bytes() == b"unrelated working material\n"
    assert other_resource.read_bytes() == b"foreign service material\n"
    assert foreign_rows(tools) == unrelated


@pytest.mark.parametrize("fault", ["version", "owner", "missing"])
def test_cancelled_cleanup_rechecks_current_metadata_before_material_retirement(project, monkeypatch, fault):
    owner, tools = draft(project, "fresh")
    root = owner.state / owner.paths["standalone_tasks"] / "DRAFT"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "internal-plan.txt"
    source.write_bytes(b"retain until current ownership is verified\n")
    _, keep = create_unrelated_task(project, root)
    unrelated = foreign_rows(tools)
    prepare = owner.delivery_effects.prepare
    observed = []

    def race_after_observation(record, args):
        prepared = prepare(record, args)
        assert record["status"] == "cancelled"
        with sqlite3.connect(owner.store.database.path) as db:
            before = db.execute("SELECT version,claimed_by FROM tasks WHERE id='DRAFT'").fetchone()
            assert before == (record["version"], None)
            observed.append(before)
            if fault == "version":
                db.execute("UPDATE tasks SET version=version+1 WHERE id='DRAFT'")
            elif fault == "owner":
                db.execute("INSERT INTO sessions(id,task_id) VALUES('race-owner',NULL)")
                db.execute("UPDATE tasks SET claimed_by='race-owner' WHERE id='DRAFT'")
            else:
                db.execute("DELETE FROM tasks WHERE id='DRAFT'")
        return prepared

    monkeypatch.setattr(owner.delivery_effects, "prepare", race_after_observation)
    expected = ("Задача не найдена: DRAFT" if fault == "missing"
                else "Delivery Task owner/version changed before agreement")
    with pytest.raises(PoiseError, match="^" + expected + "$"):
        tools.invoke(request("cancel", {"reason": "Discard this Task without a customer result."}))

    assert len(observed) == 1
    assert source.read_bytes() == b"retain until current ownership is verified\n"
    assert keep.read_bytes() == b"unrelated working material\n"
    assert foreign_rows(tools) == unrelated
    with sqlite3.connect(owner.store.database.path) as db:
        actual = db.execute("SELECT version,claimed_by FROM tasks WHERE id='DRAFT'").fetchone()
        if fault == "version":
            assert actual == (observed[0][0] + 1, None)
        elif fault == "owner":
            assert actual == (observed[0][0], "race-owner")
            assert db.execute("SELECT task_id FROM sessions WHERE id='race-owner'").fetchone() == (None,)
        else:
            assert actual is None
        assert db.execute("SELECT COUNT(*) FROM journal WHERE task_id='DRAFT' AND event='task_delivery.state'").fetchone()[0] == 0
