from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest

from conftest import WorkPoise, git
from poise.common import PoiseError


def _runtime(project, session="SNAPSHOT-INDEX"):
    return WorkPoise(project["config_path"], session)


def _real_index(project) -> Path:
    path = Path(git(project["app"], "rev-parse", "--git-path", "index"))
    return path if path.is_absolute() else project["app"] / path


def test_stale_legacy_lock_is_ignored_without_deletion(project):
    runtime = _runtime(project)
    runtime.runtime.mkdir(parents=True, exist_ok=True)
    legacy_lock = runtime.runtime / "snapshot.index.lock"
    legacy_lock.touch()
    real_index = _real_index(project)
    before = real_index.read_bytes()

    tree = runtime._tree(project["app"])

    assert tree == git(project["app"], "write-tree")
    assert legacy_lock.exists()
    assert real_index.read_bytes() == before
    assert not [path for path in runtime.runtime.iterdir() if path.is_dir()]


def test_failed_invocation_cleans_only_its_directory_and_retry_succeeds(
    project,
    monkeypatch,
):
    runtime = _runtime(project, "SNAPSHOT-FAILURE")
    runtime.runtime.mkdir(parents=True, exist_ok=True)
    foreign = runtime.runtime / "snapshot.index.foreign-owned"
    foreign.mkdir()
    foreign_lock = foreign / "index.lock"
    foreign_lock.write_text("foreign owner\n", encoding="utf-8")
    original = runtime._git
    captured_indexes = []
    fail_once = True

    def observed_git(cwd, *args, env=None):
        nonlocal fail_once
        index = Path(env["GIT_INDEX_FILE"])
        if index not in captured_indexes:
            captured_indexes.append(index)
        if args[0] == "add" and fail_once:
            fail_once = False
            Path(f"{index}.lock").touch()
            raise PoiseError("injected snapshot failure")
        return original(cwd, *args, env=env)

    monkeypatch.setattr(runtime, "_git", observed_git)

    with pytest.raises(PoiseError, match="injected snapshot failure"):
        runtime._tree(project["app"])

    first = captured_indexes[0]
    assert first.parent != runtime.runtime
    assert not first.parent.exists()
    assert runtime._tree(project["app"]) == git(project["app"], "write-tree")
    assert captured_indexes[-1] != first
    assert foreign_lock.read_text(encoding="utf-8") == "foreign owner\n"


def test_concurrent_session_snapshots_use_distinct_git_indexes(project, monkeypatch):
    runtime = _runtime(project, "SNAPSHOT-CONCURRENT")
    original = runtime._git
    barrier = Barrier(2)
    capture_lock = Lock()
    indexes = []

    def observed_git(cwd, *args, env=None):
        if args[0] == "read-tree":
            with capture_lock:
                indexes.append(env["GIT_INDEX_FILE"])
            barrier.wait()
        return original(cwd, *args, env=env)

    monkeypatch.setattr(runtime, "_git", observed_git)

    with ThreadPoolExecutor(max_workers=2) as pool:
        trees = list(pool.map(lambda _: runtime._tree(project["app"]), range(2)))

    assert len(set(indexes)) == 2
    assert trees[0] == trees[1] == git(project["app"], "write-tree")
    assert not runtime.runtime.exists() or not list(runtime.runtime.iterdir())


def test_snapshot_captures_all_worktree_states_without_changing_real_index(project):
    runtime = _runtime(project, "SNAPSHOT-REAL-INDEX")
    app = project["app"]
    (app / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(app, "add", "staged.txt")
    (app / "src" / "double.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (app / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    real_index = _real_index(project)
    before = real_index.read_bytes()

    tree = runtime._tree(app)
    changed = set(
        git(app, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", tree).splitlines()
    )

    assert {"staged.txt", "src/double.py", "untracked.txt"} <= changed
    assert real_index.read_bytes() == before


def test_snapshot_survives_overlapping_session_cleanup_and_sibling_snapshot(project, monkeypatch):
    """Force cleanup between read-tree and add, without timing sleeps."""
    from threading import Event

    runtime = _runtime(project, "SNAPSHOT-CLEANUP-RACE")
    original = runtime._git
    reached = Barrier(2, timeout=10)
    resume = Event()
    captured = []
    capture_lock = Lock()
    before = _real_index(project).read_bytes()

    def observed_git(cwd, *args, env=None):
        answer = original(cwd, *args, env=env)
        if args[0] == "read-tree":
            with capture_lock:
                captured.append(Path(env["GIT_INDEX_FILE"]))
                first = len(captured) == 1
            if first:
                reached.wait()
                assert resume.wait(10), "main thread did not release snapshot"
        return answer

    monkeypatch.setattr(runtime, "_git", observed_git)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime._tree, project["app"])
        try:
            reached.wait()
            runtime._cleanup_runtime()
            first_still_exists = captured[0].is_file()
            second_tree = runtime._tree(project["app"])
        finally:
            resume.set()
        first_tree = pending.result(timeout=10)
    assert first_still_exists
    assert captured[0] != captured[1]
    assert first_tree == second_tree == git(project["app"], "write-tree")
    assert all(not path.parent.exists() for path in captured)
    assert _real_index(project).read_bytes() == before


def test_snapshot_cleanup_is_idempotent_for_absent_invocation(project, monkeypatch):
    import shutil

    runtime = _runtime(project, "SNAPSHOT-ALREADY-CLEAN")
    original = runtime._git

    def observed_git(cwd, *args, env=None):
        tree = original(cwd, *args, env=env)
        if args[0] == "write-tree":
            shutil.rmtree(Path(env["GIT_INDEX_FILE"]).parent)
        return tree

    monkeypatch.setattr(runtime, "_git", observed_git)
    assert runtime._tree(project["app"]) == git(project["app"], "write-tree")


def test_absent_snapshot_cleanup_preserves_the_original_git_failure(project, monkeypatch):
    import shutil

    runtime = _runtime(project, "SNAPSHOT-ORIGINAL-ERROR")
    original = runtime._git

    def observed_git(cwd, *args, env=None):
        if args[0] == "add":
            shutil.rmtree(Path(env["GIT_INDEX_FILE"]).parent)
            raise PoiseError("real git operation failed")
        return original(cwd, *args, env=env)

    monkeypatch.setattr(runtime, "_git", observed_git)
    with pytest.raises(PoiseError, match="real git operation failed"):
        runtime._tree(project["app"])


def test_snapshot_cleanup_does_not_suppress_permission_failure(project, monkeypatch):
    import poise.runtime as module

    runtime = _runtime(project, "SNAPSHOT-PERMISSIONS")

    def denied(path, *args, **kwargs):
        raise PermissionError("snapshot cleanup access denied")

    monkeypatch.setattr(module.shutil, "rmtree", denied)
    with pytest.raises(PermissionError, match="snapshot cleanup access denied"):
        runtime._tree(project["app"])


def test_overlapping_session_cleanup_is_idempotent(project, monkeypatch):
    import poise.runtime as module

    runtime = _runtime(project, "SESSION-DOUBLE-CLEANUP")
    runtime.runtime.mkdir(parents=True)
    original = module.shutil.rmtree
    reached = Barrier(2, timeout=10)

    def overlapping(path, *args, **kwargs):
        if Path(path) == runtime.runtime:
            reached.wait()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", overlapping)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime._cleanup_runtime) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert not runtime.runtime.exists()
