"""Completion and replay use the configured workspace root and preserve neighbors."""
import subprocess

import pytest

from conftest import WorkPoise, git
from poise.modules.foundation.errors import PoiseError

from .helpers import integration_input, optional_ref, prepare_completed_task, request, source_change


def neighbor_change(tree):
    (tree / "src/neighbor.py").write_text("VALUE = 2\n", encoding="utf-8")


def binding(runtime, actor):
    with runtime.store.database.read_transaction() as db:
        row = db.execute("SELECT task_id FROM sessions WHERE id=?", (actor,)).fetchone()
    return None if row is None else row[0]


def porcelain(tree):
    return subprocess.check_output(["git", "-C", str(tree), "status", "--porcelain"])


def neighbor_snapshot(runtime, tree):
    with runtime.store.database.read_transaction() as db:
        task = tuple(db.execute(
            "SELECT status,version,claimed_by,current_submission_id,metadata FROM tasks WHERE id=?",
            ("T2",),
        ).fetchone())
        execution = tuple(db.execute(
            "SELECT version,data FROM task_execution WHERE task_id=?", ("T2",),
        ).fetchone())
    return (
        task, execution, binding(runtime, "neighbor-owner"),
        git(tree, "rev-parse", "HEAD"), git(tree, "symbolic-ref", "HEAD"),
        porcelain(tree), (tree / "src/neighbor.py").read_bytes(),
    )


def reconciliations(runtime):
    with runtime.store.database.read_transaction() as db:
        return [tuple(row) for row in db.execute(
            "SELECT session_id,task_id,data FROM journal WHERE event=? ORDER BY seq",
            ("ownership.integrated_worktree_reconciled",),
        )]


def scenario(project, root_kind):
    state = project["root"] / "state"
    if root_kind == "repository":
        root = project["app"] / ".worktrees"
        project["cfg"]["paths"]["worktrees"] = str(root)
    else:
        root = state / "worktrees"
    tools, tree, source = prepare_completed_task(project, source_change)
    assert tree == root / "T1"
    assert root_kind != "repository" or not root.is_relative_to(state)
    _, neighbor, neighbor_commit = prepare_completed_task(
        project, neighbor_change, task_id="T2",
    )
    owner = WorkPoise(project["config_path"], "neighbor-owner")
    owner.ownership.acquire_worktree("T2")
    assert binding(tools.runtime, "neighbor-owner") == "T2"
    assert git(neighbor, "rev-parse", "HEAD") == neighbor_commit
    (neighbor / "src/neighbor.py").write_text("VALUE = 'unfinished'\n", encoding="utf-8")
    assert porcelain(neighbor) == b" M src/neighbor.py\n"
    record = tools.runtime.task_queries.record("T1")
    assert record["status"] == "completed"
    assert record["claimed_by"] is None
    assert record["last_report"]["commit"] == source
    packet = request("integrate", integration_input(project, source))
    return tools, tree, source, neighbor, packet


@pytest.mark.parametrize("root_kind", ["state", "repository"])
@pytest.mark.parametrize("mode", ["completion", "replay"])
def test_configured_root_reconciles_only_its_completed_task(project, root_kind, mode):
    tools, tree, source, neighbor, packet = scenario(project, root_kind)
    predecessor = WorkPoise(project["config_path"], "predecessor")
    original = tools.invoke(packet) if mode == "replay" else None
    predecessor.ownership.acquire_worktree("T1")
    assert binding(tools.runtime, "predecessor") == "T1"
    before = neighbor_snapshot(tools.runtime, neighbor)

    result = tools.invoke(packet)

    assert result["status"] == "integrated"
    assert result["cleanup"] == {
        "task_worktree": "removed", "task_branch": "deleted", "temporary_backups": "removed",
    }
    if original is not None:
        assert result == {**original, "replayed": True}
    assert not tree.exists()
    assert optional_ref(project["app"], "refs/heads/tasks/T1") is None
    assert git(project["app"], "merge-base", "--is-ancestor", source, "main") == ""
    assert (project["app"] / "src/feature.py").read_bytes() == b"VALUE = 1\n"
    assert binding(tools.runtime, "predecessor") is None
    assert neighbor_snapshot(tools.runtime, neighbor) == before
    audit = reconciliations(tools.runtime)
    assert len(audit) == 1
    assert audit[0][:2] == ("worker", "T1")

    assert tools.invoke(packet) == {**result, "replayed": True}
    assert reconciliations(tools.runtime) == audit
    assert neighbor_snapshot(tools.runtime, neighbor) == before


@pytest.mark.parametrize("root_kind", ["state", "repository"])
def test_configured_root_replay_preserves_recreated_directory(project, root_kind):
    tools, tree, _, neighbor, packet = scenario(project, root_kind)
    assert tools.invoke(packet)["status"] == "integrated"
    tree.mkdir()
    marker = tree / "recreated.txt"
    marker.write_bytes(b"preserve recreated material\n")
    predecessor = WorkPoise(project["config_path"], "predecessor")
    predecessor.ownership.acquire_worktree("T1")
    before = neighbor_snapshot(tools.runtime, neighbor)
    execution = tools.runtime.task_queries.record("T1")

    with pytest.raises(PoiseError, match="Worktree resource was recreated after cleanup"):
        tools.invoke(packet)

    assert marker.read_bytes() == b"preserve recreated material\n"
    assert binding(tools.runtime, "predecessor") == "T1"
    assert neighbor_snapshot(tools.runtime, neighbor) == before
    assert tools.runtime.task_queries.record("T1") == execution
    assert reconciliations(tools.runtime) == []
