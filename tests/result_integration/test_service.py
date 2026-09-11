from pathlib import Path
import subprocess

import pytest

from conftest import git
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.modules.foundation.errors import PoiseError
from poise.runtime import Poise

from .helpers import integration_input, prepare_completed_task, request


def _git_bytes(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def _index_bytes(root):
    index = Path(git(root, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = root / index
    return index.read_bytes()


def _local_snapshot(root, paths):
    selected = tuple(paths)
    return {
        "bytes": {path: (root / path).read_bytes() for path in selected},
        "status": _git_bytes(root, "status", "--porcelain=v2", "-z", "--", *selected),
        "index": _git_bytes(root, "ls-files", "--stage", "-z", "--", *selected),
        "cached": _git_bytes(root, "diff", "--cached", "--binary", "--", *selected),
        "unstaged": _git_bytes(root, "diff", "--binary", "--", *selected),
    }


def _prepare_disjoint_local_changes(root):
    tracked = ("local-staged.txt", "local-unstaged.txt")
    for path in tracked:
        (root / path).write_text("base\n")
    git(root, "add", *tracked)
    git(root, "commit", "-m", "test: local change fixtures")
    return tracked


def _dirty_disjoint_target(root):
    (root / "local-staged.txt").write_text("staged user bytes\n")
    git(root, "add", "local-staged.txt")
    (root / "local-unstaged.txt").write_text("unstaged user bytes\n")
    (root / "local-untracked.txt").write_text("untracked user bytes\n")
    paths = ("local-staged.txt", "local-unstaged.txt", "local-untracked.txt")
    return paths, _local_snapshot(root, paths)


def _assert_no_integration_record(tools):
    with pytest.raises(PoiseError, match="not found"):
        tools.invoke(request("show", {"queries": [{
            "id": "integration",
            "kind": "integration",
            "task_id": "T1",
            "request_id": "integrate-1",
        }]}))


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

    with pytest.raises(PoiseError, match="target.*changed|expected target"):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "HEAD") == target_after
    assert source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1")


@pytest.mark.parametrize("invalid_state", ["unaccepted", "wrong_source", "dirty_source", "dirty_target", "wrong_branch"])
def test_source_task_branch_commit_and_both_worktrees_are_validated_before_merge(project, invalid_state):
    tools, source_worktree, source = prepare_completed_task(
        project,
        lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n"),
        accept=invalid_state != "unaccepted",
    )
    payload = integration_input(project, source)
    if invalid_state == "wrong_source":
        payload["expected_source_commit"] = "f" * 40
    elif invalid_state == "dirty_source":
        (source_worktree / "untracked.txt").write_text("dirty\n")
    elif invalid_state == "dirty_target":
        (project["app"] / "src" / "feature.py").write_text("dirty\n")
    elif invalid_state == "wrong_branch":
        git(source_worktree, "branch", "-m", "unexpected-source")
    target_before = git(project["app"], "rev-parse", "HEAD")

    with pytest.raises(PoiseError):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "HEAD") == target_before
    assert source_worktree.exists()


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
    pending_history = pending["history"]
    assert pending_history[-1]["status"] == "cleanup_pending"
    git(project["app"], "worktree", "unlock", str(source_worktree))

    completed = tools.invoke(request("integrate", payload))

    assert completed["status"] == "integrated"
    assert completed["target_after"] == integrated_head
    assert not source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1") == ""

    reader = WorkTools(Poise(project["config_path"], "reader"))
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
    assert [entry["status"] for entry in saved["history"]][-2:] == ["cleanup_pending", "integrated"]
    task = reader.task_queries.record("T1")
    assert task["status"] == "completed"
    assert task["result_commit"] == source


def test_disjoint_dirty_target_preserves_index_worktree_and_commit(project):
    _prepare_disjoint_local_changes(project["app"])
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    paths, before = _dirty_disjoint_target(project["app"])

    result = tools.invoke(request("integrate", integration_input(project, source)))

    assert result["status"] == "integrated"
    assert _local_snapshot(project["app"], paths) == before
    integrated_paths = set(git(project["app"], "diff", "--name-only", "HEAD^1", "HEAD").splitlines())
    assert integrated_paths == {"src/feature.py"}
    assert not source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1") == ""


@pytest.mark.parametrize(
    ("scenario", "reported_paths"),
    [
        ("tracked", ("src/double.py",)),
        ("untracked", ("src/collision.txt",)),
        ("local_file_parent", ("src/collision", "src/collision/child.txt")),
        ("local_descendant", ("src/collision", "src/collision/child.txt")),
        ("rename_source", ("src/double.py",)),
        ("delete_source", ("src/double.py",)),
    ],
)
def test_overlapping_dirty_target_paths_are_rejected_before_mutation(
    project, scenario, reported_paths
):
    def source_change(tree):
        if scenario == "tracked":
            (tree / "src" / "double.py").write_text("SOURCE = 1\n")
        elif scenario == "untracked":
            (tree / "src" / "collision.txt").write_text("source\n")
        elif scenario == "local_file_parent":
            (tree / "src" / "collision").mkdir()
            (tree / "src" / "collision" / "child.txt").write_text("source\n")
        elif scenario == "local_descendant":
            (tree / "src" / "collision").write_text("source\n")
        elif scenario == "rename_source":
            (tree / "src" / "double.py").rename(tree / "src" / "renamed.py")
        elif scenario == "delete_source":
            (tree / "src" / "double.py").unlink()

    tools, source_worktree, source = prepare_completed_task(project, source_change)
    if scenario in ("tracked", "rename_source", "delete_source"):
        local_paths = ("src/double.py",)
        (project["app"] / "src" / "double.py").write_text("local tracked bytes\n")
        git(project["app"], "add", "src/double.py")
    elif scenario == "untracked":
        local_paths = ("src/collision.txt",)
        (project["app"] / "src" / "collision.txt").write_text("local untracked bytes\n")
    elif scenario == "local_file_parent":
        local_paths = ("src/collision",)
        (project["app"] / "src" / "collision").write_text("local parent bytes\n")
    else:
        local_paths = ("src/collision/child.txt",)
        (project["app"] / "src" / "collision").mkdir()
        (project["app"] / "src" / "collision" / "child.txt").write_text(
            "local child bytes\n"
        )
    before_head = git(project["app"], "rev-parse", "HEAD")
    before_index = _index_bytes(project["app"])
    before_local = _local_snapshot(project["app"], local_paths)

    with pytest.raises(PoiseError) as failure:
        tools.invoke(request("integrate", integration_input(project, source)))

    message = str(failure.value).lower()
    for path in reported_paths:
        assert path.lower() in message
    assert any(word in message for word in ("commit", "move", "remove", "resolve"))
    assert git(project["app"], "rev-parse", "HEAD") == before_head
    assert _index_bytes(project["app"]) == before_index
    assert _local_snapshot(project["app"], local_paths) == before_local
    assert source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1")
    _assert_no_integration_record(tools)


@pytest.mark.parametrize(
    ("operation", "marker", "recovery"),
    [
        ("merge", "MERGE_HEAD", "git merge --abort"),
        ("cherry-pick", "CHERRY_PICK_HEAD", "git cherry-pick --abort"),
    ],
)
def test_unresolved_target_operation_is_rejected_before_mutation(
    project, operation, marker, recovery
):
    operation_commit = None
    if operation == "cherry-pick":
        git(project["app"], "switch", "-c", "pending-operation")
        (project["app"] / "src" / "double.py").write_text("operation side\n")
        git(project["app"], "add", "src/double.py")
        git(project["app"], "commit", "-m", "test: pending cherry-pick source")
        operation_commit = git(project["app"], "rev-parse", "HEAD")
        git(project["app"], "switch", "main")
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    if operation == "merge":
        git(project["app"], "merge", "--no-ff", "--no-commit", source)
    else:
        (project["app"] / "src" / "double.py").write_text("target side\n")
        git(project["app"], "add", "src/double.py")
        git(project["app"], "commit", "-m", "test: conflicting cherry-pick target")
        receipt = subprocess.run(
            ["git", "-C", str(project["app"]), "cherry-pick", operation_commit],
            capture_output=True,
            text=True,
        )
        assert receipt.returncode == 1, receipt.stdout + receipt.stderr
    before_head = git(project["app"], "rev-parse", "HEAD")
    before_operation_head = git(project["app"], "rev-parse", marker)
    before_index = _index_bytes(project["app"])
    before_status = _git_bytes(project["app"], "status", "--porcelain=v2", "-z")

    with pytest.raises(PoiseError) as failure:
        tools.invoke(request("integrate", integration_input(project, source)))

    message = str(failure.value)
    assert marker in message
    assert recovery in message
    assert git(project["app"], "rev-parse", "HEAD") == before_head
    assert git(project["app"], "rev-parse", marker) == before_operation_head
    assert _index_bytes(project["app"]) == before_index
    assert _git_bytes(project["app"], "status", "--porcelain=v2", "-z") == before_status
    assert source_worktree.exists()
    _assert_no_integration_record(tools)


def test_dirty_target_conflict_retry_and_cleanup_preserve_local_state(project):
    _prepare_disjoint_local_changes(project["app"])

    def source_change(tree):
        (tree / "src" / "double.py").write_text("VALUE = 'source'\n")

    tools, source_worktree, source = prepare_completed_task(project, source_change)
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "feat: conflicting target result")
    paths, before = _dirty_disjoint_target(project["app"])
    initial = integration_input(project, source)

    conflict = tools.invoke(request("integrate", initial))

    assert conflict["status"] == "awaiting_resolution"
    assert conflict["conflicts"] == ["src/double.py"]
    assert _local_snapshot(project["app"], paths) == before
    (project["app"] / "src" / "double.py").write_text("VALUE = 'resolved'\n")
    resolutions = [{
        "path": "src/double.py",
        "resolution": "Combined accepted source and target intent.",
    }]

    integrated = tools.invoke(request("integrate", {**initial, "resolutions": resolutions}))

    assert integrated["status"] == "integrated"
    assert _local_snapshot(project["app"], paths) == before
    assert not source_worktree.exists()
    replay = tools.invoke(request("integrate", {
        **initial,
        "expected_target_commit": integrated["target_after"],
        "resolutions": resolutions,
    }))
    assert replay["status"] == "integrated"
    assert replay["replayed"] is True
    assert replay["target_after"] == integrated["target_after"]
    assert _local_snapshot(project["app"], paths) == before


def test_dirty_target_post_preflight_failure_is_recoverable_and_never_cleans_source(
    project, monkeypatch
):
    _prepare_disjoint_local_changes(project["app"])
    tools, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    paths, before = _dirty_disjoint_target(project["app"])
    payload = integration_input(project, source)
    original_run = RuntimeResultIntegration._run
    failed = False

    def fail_first_merge(self, cwd, *args, env=None):
        nonlocal failed
        if args[0] == "merge" and not failed:
            failed = True
            return {
                "argv": ["git", "-C", str(cwd), *args],
                "actual_exit_code": 128,
                "stdout": "",
                "stderr": "injected merge failure",
            }
        return original_run(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", fail_first_merge)

    blocked = tools.invoke(request("integrate", payload))

    assert blocked["status"] == "blocked"
    assert blocked["failure"]["reason"] == "merge_failed_without_conflicts"
    assert "injected merge failure" in blocked["failure"]["receipt"]["stderr"]
    assert _local_snapshot(project["app"], paths) == before
    assert source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1")

    integrated = tools.invoke(request("integrate", payload))

    assert integrated["status"] == "integrated"
    assert _local_snapshot(project["app"], paths) == before
    assert not source_worktree.exists()
    assert git(project["app"], "branch", "--list", "tasks/T1") == ""
