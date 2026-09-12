"""Task 0057 contract: finish on the existing child branch and worktree."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import subprocess
from threading import Barrier

import pytest

from conftest import WorkPoise as Poise
from conftest import git
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
from poise.modules.foundation.errors import PoiseError

from .helpers import (
    advance_ref_with_same_tree,
    conflicting_source_change,
    integration_guard_method,
    integration_input,
    optional_ref,
    prepare_completed_task,
    request,
    source_change,
)


def _fingerprint(root: Path) -> dict:
    index = Path(git(root, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = root / index
    listed = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).decode().split("\0")
    paths = sorted(path for path in listed if path)
    files = {}
    for path in paths:
        selected = root / path
        if not selected.exists() and not selected.is_symlink():
            files[path] = None
            continue
        info = selected.lstat()
        content = os.readlink(selected).encode() if selected.is_symlink() else selected.read_bytes()
        files[path] = (stat.S_IMODE(info.st_mode), content)
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "branch": git(root, "symbolic-ref", "--short", "HEAD"),
        "index": index.read_bytes(),
        "files": files,
        "status": subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v2", "-z"]),
    }


def test_reuses_task_branch_and_worktree_for_update_checks_and_ff_only(project, monkeypatch):
    method = integration_guard_method()
    tools, task_worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=[method["id"]]
    )
    task_branch = git(task_worktree, "symbolic-ref", "--short", "HEAD")
    included_target = git(project["app"], "rev-parse", "refs/heads/main")
    calls = []
    original = RuntimeTaskResourceCleanup._run

    def record(self, cwd, *args, env=None):
        calls.append((str(cwd), args))
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", record)
    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "integrated"
    assert result["accepted_commit"] == accepted
    assert result["last_included_target"] == included_target
    assert result["integration_head"] == result["target_after"]
    assert "integration_branch" not in result
    assert "integration_worktree" not in result
    assert result["cleanup"] == {
        "task_worktree": "removed",
        "task_branch": "deleted",
        "temporary_backups": "removed",
    }
    assert not task_worktree.exists()
    assert optional_ref(project["app"], f"refs/heads/{task_branch}") is None
    assert not any(args[:2] == ("worktree", "add") for _, args in calls)
    assert not any(args and args[0] == "update-ref" and "refs/heads/main" in args for _, args in calls)
    assert any(
        cwd == str(project["app"])
        and args == ("merge", "--ff-only", task_branch)
        for cwd, args in calls
    )
    output = Path(result["checks"][0]["stdout"]).read_text()
    assert f"cwd={task_worktree}" in output
    assert f"branch={task_branch}" in output


def test_conflict_is_resolved_and_committed_only_in_existing_task_worktree(project):
    method = integration_guard_method()
    tools, task_worktree, accepted = prepare_completed_task(
        project, conflicting_source_change, methods=[method], checks=[method["id"]]
    )
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "feat: conflicting target")
    target = git(project["app"], "rev-parse", "HEAD")
    payload = integration_input(project, accepted)

    waiting = tools.invoke(request("integrate", payload))

    assert waiting["status"] == "awaiting_resolution"
    assert waiting["conflicts"] == ["src/double.py"]
    assert waiting["last_included_target"] == target
    assert optional_ref(task_worktree, "MERGE_HEAD") == target
    assert optional_ref(project["app"], "MERGE_HEAD") is None
    (task_worktree / "src" / "double.py").write_text("VALUE = 'resolved'\n")

    completed = tools.invoke(request("integrate", {
        **payload,
        "resolutions": [{
            "path": "src/double.py",
            "resolution": "Preserve accepted behavior with the current target API.",
        }],
    }))

    assert completed["status"] == "integrated"
    assert git(project["app"], "show", "HEAD:src/double.py") == "VALUE = 'resolved'"
    assert Path(completed["checks"][0]["stdout"]).read_text().find(str(task_worktree)) >= 0


def test_target_drift_reupdates_same_branch_and_reruns_checks(project, monkeypatch):
    method = integration_guard_method()
    tools, task_worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=[method["id"]]
    )
    original = RuntimeResultIntegration._run
    drifted = []

    def drift_before_first_ff(self, cwd, *args, env=None):
        if args[:2] == ("merge", "--ff-only") and not drifted:
            current = git(project["app"], "rev-parse", "refs/heads/main")
            drifted.append(advance_ref_with_same_tree(project["app"], current, "test: target drift"))
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", drift_before_first_ff)
    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "integrated"
    assert result["publication"]["drift_retries"] == 1
    assert len(result["checks"]) == 2
    assert all(item["passed"] for item in result["checks"])
    assert git(project["app"], "merge-base", "--is-ancestor", drifted[0], result["target_after"]) == ""
    assert not task_worktree.exists()


def test_blocked_ff_only_proves_all_main_worktree_state_unchanged(project):
    tools, task_worktree, accepted = prepare_completed_task(project, source_change)
    root = project["app"]
    (root / "staged.txt").write_text("staged\n")
    git(root, "add", "staged.txt")
    (root / "src" / "double.py").write_text("unstaged\n")
    (root / "untracked.txt").write_text("untracked\n")
    (root / "src" / "feature.py").write_text("operator overlap\n")
    deleted = root / "AGENTS.md"
    deleted.unlink()
    mode_path = root / "src" / "double.py"
    mode_path.chmod(mode_path.stat().st_mode | stat.S_IXUSR)
    before = _fingerprint(root)

    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "blocked"
    assert result["phase"] == "publication_failed"
    assert result["failure"]["reason"] == "fast_forward_blocked"
    assert result["failure"]["receipt"]["worktree_unchanged"] is True
    assert _fingerprint(root) == before
    assert task_worktree.exists()


def test_publication_refuses_to_advance_a_non_target_checked_out_branch(project):
    tools, task_worktree, accepted = prepare_completed_task(project, source_change)
    root = project["app"]
    git(root, "switch", "-c", "operator-branch")
    before = _fingerprint(root)

    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "blocked"
    assert result["failure"]["reason"] == "target_not_checked_out"
    assert result["failure"]["receipt"]["worktree_unchanged"] is True
    assert _fingerprint(root) == before
    assert git(root, "rev-parse", "refs/heads/main") == result["last_included_target"]
    assert task_worktree.exists()


def test_crash_after_ff_only_reconciles_without_second_publication(project, monkeypatch):
    tools, task_worktree, accepted = prepare_completed_task(project, source_change)
    payload = integration_input(project, accepted)
    original = RuntimeResultIntegration._run
    calls = 0
    crashed = False

    def crash_after_ff(self, cwd, *args, env=None):
        nonlocal calls, crashed
        receipt = original(self, cwd, *args, env=env)
        if args[:2] == ("merge", "--ff-only"):
            calls += 1
            if receipt["actual_exit_code"] == 0 and not crashed:
                crashed = True
                raise RuntimeError("injected crash after ff-only")
        return receipt

    monkeypatch.setattr(RuntimeResultIntegration, "_run", crash_after_ff)
    with pytest.raises(RuntimeError, match="after ff-only"):
        tools.invoke(request("integrate", payload))

    restarted = WorkTools(Poise(project["config_path"], "restarted-integrator"))
    result = restarted.invoke(request("integrate", payload))

    assert result["status"] == "integrated"
    assert result["publication"]["recovered"] is True
    assert calls == 1
    assert not task_worktree.exists()


def test_exact_legacy_blocked_request_recovers_without_rewriting_history(project):
    tools, task_worktree, accepted = prepare_completed_task(
        project, source_change, task_id="0048"
    )
    payload = integration_input(
        project, accepted, request_id="integrate-0048-1", task_id="0048"
    )
    legacy_history = [
        {"status": "prepared"},
        {"status": "running", "event": "merge_started"},
        {"status": "blocked", "event": "merge_blocked", "details": {
            "reason": "merge_failed_without_conflicts",
            "receipt": {"actual_exit_code": 2, "stdout": "", "stderr": "Aborting\n"},
        }},
    ]
    legacy = {
        "kind": "result_integration",
        "intent": {key: value for key, value in payload.items() if key != "resolutions"},
        "status": "blocked",
        "version": 2,
        "target_before": payload["expected_target_commit"],
        "target_after": None,
        "conflicts": [],
        "resolutions": [],
        "merge": {"preflight": {"head": payload["expected_target_commit"]}},
        "failure": legacy_history[-1]["details"],
        "cleanup": {"worktree": "pending", "branch": "pending"},
        "history": legacy_history,
    }
    with tools.runtime.store.unit_of_work() as uow:
        data, version = uow.execution.load("0048")
        data["pending"] = legacy
        uow.execution.save("0048", data, version)

    result = tools.invoke(request("integrate", payload))

    assert result["status"] == "integrated"
    assert result["accepted_commit"] == accepted
    assert result["history"][:len(legacy_history)] == legacy_history
    assert any(item.get("event") == "legacy_recovery_started" for item in result["history"])
    assert not task_worktree.exists()


def test_concurrent_publications_are_serialized_and_rebase_the_loser(project, monkeypatch):
    def first_change(tree):
        (tree / "src" / "first.py").write_text("FIRST = True\n")

    def second_change(tree):
        (tree / "src" / "second.py").write_text("SECOND = True\n")

    _, first_worktree, first = prepare_completed_task(
        project, first_change, task_id="T1"
    )
    _, second_worktree, second = prepare_completed_task(
        project, second_change, task_id="T2"
    )
    first_payload = integration_input(project, first, task_id="T1", request_id="first")
    second_payload = integration_input(project, second, task_id="T2", request_id="second")
    barrier = Barrier(2)
    original = RuntimeResultIntegration._run_checks

    def synchronize_candidates(self, record, run):
        checked = original(self, record, run)
        if len(checked.checks) == 0 and checked.drift_retries == 0:
            barrier.wait()
        return checked

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", synchronize_candidates)
    first_tools = WorkTools(Poise(project["config_path"], "first-integrator"))
    second_tools = WorkTools(Poise(project["config_path"], "second-integrator"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(first_tools.invoke, request("integrate", first_payload)),
            pool.submit(second_tools.invoke, request("integrate", second_payload)),
        ]
        results = [future.result() for future in futures]

    assert all(result["status"] == "integrated" for result in results)
    assert sum(result["publication"]["drift_retries"] for result in results) == 1
    assert git(project["app"], "show", "HEAD:src/first.py") == "FIRST = True"
    assert git(project["app"], "show", "HEAD:src/second.py") == "SECOND = True"
    assert not first_worktree.exists()
    assert not second_worktree.exists()


def test_failed_post_update_check_prevents_publication_and_cleanup(project):
    method = {
        **integration_guard_method(),
        "argv": [
            os.sys.executable,
            "-c",
            "import pathlib,sys;sys.exit(pathlib.Path('target-marker').exists())",
        ],
        "stdout_contains": [],
    }
    tools, task_worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=[method["id"]]
    )
    (project["app"] / "target-marker").write_text("target\n")
    git(project["app"], "add", "target-marker")
    git(project["app"], "commit", "-m", "test: advance target before integration")
    target = git(project["app"], "rev-parse", "HEAD")

    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "blocked"
    assert result["phase"] == "checks_failed"
    assert result["checks"][-1]["passed"] is False
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert task_worktree.exists()


def test_saved_intent_is_immutable_during_conflict(project):
    tools, _, accepted = prepare_completed_task(project, conflicting_source_change)
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "test: conflicting target")
    payload = integration_input(project, accepted, request_id="immutable")
    waiting = tools.invoke(request("integrate", payload))

    with pytest.raises(PoiseError, match="intent is immutable"):
        tools.invoke(request("integrate", {
            **payload,
            "authorization": "Changed authorization cannot replace persisted intent.",
        }))

    shown = tools.invoke(request("show", {"queries": [{
        "id": "saved", "kind": "integration", "task_id": "T1",
        "request_id": "immutable",
    }]}))["results"][0]["value"]
    assert shown["accepted_commit"] == accepted
    assert shown["history"] == waiting["history"]


def test_cleanup_replays_only_the_failed_owned_step(project, monkeypatch):
    tools, task_worktree, accepted = prepare_completed_task(project, source_change)
    payload = integration_input(project, accepted)
    original = RuntimeResultIntegration._run
    blocked_once = False

    def block_first_removal(self, cwd, *args, env=None):
        nonlocal blocked_once
        if args[:2] == ("worktree", "remove") and not blocked_once:
            blocked_once = True
            return {"argv": list(args), "actual_exit_code": 1,
                    "stdout": "", "stderr": "injected cleanup failure"}
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeTaskResourceCleanup, "_run", block_first_removal)
    pending = tools.invoke(request("integrate", payload))
    assert pending["status"] == "cleanup_pending"
    assert pending["cleanup"]["task_worktree"] == "blocked"
    assert task_worktree.exists()

    monkeypatch.setattr(RuntimeTaskResourceCleanup, "_run", original)
    completed = WorkTools(Poise(project["config_path"], "cleanup-retry")).invoke(
        request("integrate", payload)
    )
    assert completed["status"] == "integrated"
    assert not task_worktree.exists()


def test_terminal_no_op_is_reconciled_and_cleaned(project):
    tools, task_worktree, accepted = prepare_completed_task(project, source_change)
    branch = git(task_worktree, "symbolic-ref", "--short", "HEAD")
    git(project["app"], "merge", "--ff-only", branch)
    payload = integration_input(project, accepted)

    result = tools.invoke(request("integrate", payload))

    assert result["status"] == "integrated"
    assert result["publication"]["no_op"] is True
    assert result["target_after"] == accepted
    assert not task_worktree.exists()
