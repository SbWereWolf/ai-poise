from pathlib import Path

import pytest

from conftest import git
from harness.application.work import WorkTools
from harness.modules.foundation.errors import HarnessError
from harness.runtime import Harness

from .helpers import integration_input, prepare_completed_task, request


def test_completed_result_is_integrated_and_source_worktree_and_branch_are_removed(project):
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    target_before = git(project["app"], "rev-parse", "HEAD")

    result = tools.invoke(request("integrate", integration_input(project, source)))

    assert result["status"] == "integrated"
    assert result["task"] == "T1"
    assert result["source_commit"] == source
    assert result["target_before"] == target_before
    assert result["target_after"] == git(project["app"], "rev-parse", "HEAD")
    assert result["cleanup"] == {"worktree": "removed", "branch": "deleted"}
    assert not source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1") == ""
    assert git(project["app"], "merge-base", "--is-ancestor", source, "HEAD") == ""

    replay = tools.invoke(request("integrate", integration_input(project, source)))
    assert replay["status"] == "integrated" and replay["replayed"] is True
    assert replay["target_after"] == result["target_after"]


def test_conflict_is_persisted_and_same_operation_continues_after_resolution(project):
    def source_change(tree):
        (tree / "src" / "double.py").write_text("VALUE = 'source'\n")

    tools, source_worktree, source = prepare_completed_task(project, source_change)
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "feat: conflicting target result")
    initial = integration_input(project, source)

    conflict = tools.invoke(request("integrate", initial))

    assert conflict["status"] == "awaiting_resolution"
    assert conflict["conflicts"] == ["src/double.py"]
    assert source_worktree.exists()
    assert git(project["app"], "rev-parse", "MERGE_HEAD") == source
    assert git(project["app"], "branch", "--list", "tasks/T1")

    (project["app"] / "src" / "double.py").write_text("VALUE = 'resolved'\n")
    continued = tools.invoke(request("integrate", {
        **initial,
        "resolutions": [{"path": "src/double.py", "resolution": "Combined accepted source and target intent."}],
    }))

    assert continued["status"] == "integrated"
    assert continued["merge"]["conflicts"] == ["src/double.py"]
    assert continued["merge"]["resolutions"][0]["path"] == "src/double.py"
    assert len(git(project["app"], "show", "-s", "--format=%P", "HEAD").split()) == 2
    assert not source_worktree.exists()


def test_stale_target_is_rejected_without_touching_source_or_target(project):
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    stale = git(project["app"], "rev-parse", "HEAD")
    (project["app"] / "target.txt").write_text("advance\n")
    git(project["app"], "add", "target.txt")
    git(project["app"], "commit", "-m", "chore: advance target")
    target_after = git(project["app"], "rev-parse", "HEAD")
    payload = integration_input(project, source)
    payload["expected_target_commit"] = stale

    with pytest.raises(HarnessError, match="target.*changed|expected target"):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "HEAD") == target_after
    assert source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1")


def test_cleanup_interruption_is_queryable_and_retry_only_finishes_cleanup(project):
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    git(project["app"], "worktree", "lock", str(source_worktree))
    payload = integration_input(project, source)

    pending = tools.invoke(request("integrate", payload))

    assert pending["status"] == "cleanup_pending"
    integrated_head = git(project["app"], "rev-parse", "HEAD")
    assert git(project["app"], "merge-base", "--is-ancestor", source, "HEAD") == ""
    assert source_worktree.exists()
    assert pending["cleanup"]["worktree"] == "blocked"
    git(project["app"], "worktree", "unlock", str(source_worktree))

    completed = tools.invoke(request("integrate", payload))

    assert completed["status"] == "integrated"
    assert completed["target_after"] == integrated_head
    assert not source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1") == ""

    reader = WorkTools(Harness(project["config_path"], "reader"))
    shown = reader.invoke(request("show", {"queries": [{
        "id": "integration",
        "kind": "integration",
        "task_id": "T1",
        "request_id": "integrate-1",
    }]}))
    saved = shown["results"][0]["value"]
    assert saved["status"] == "integrated"
    assert saved["source_commit"] == source
    assert saved["target_after"] == integrated_head
    assert saved["cleanup"] == {"worktree": "removed", "branch": "deleted"}

