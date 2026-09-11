"""Executable contract for stable, isolated accepted-result completion."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import WorkPoise as Poise
from conftest import git
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration

from .helpers import integration_input, prepare_completed_task, request


def _optional_ref(root: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1)
    return result.stdout.strip() if result.returncode == 0 else None


def _main_checkout_fingerprint(root: Path, pinned_head: str) -> dict:
    index = Path(git(root, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = root / index
    tracked = git(root, "ls-files", "-z").split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    paths = sorted(path for path in tracked + untracked if path)
    return {
        "bytes": {path: (root / path).read_bytes() for path in paths},
        "index": index.read_bytes(),
        "cached_from_pinned": subprocess.check_output(
            ["git", "-C", str(root), "diff", "--cached", "--binary", pinned_head]
        ),
        "unstaged": subprocess.check_output(
            ["git", "-C", str(root), "diff", "--binary"]
        ),
        "untracked": subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"]
        ),
    }


def _dirty_main_checkout(root: Path) -> None:
    (root / "main-staged.txt").write_text("staged bytes\n")
    git(root, "add", "main-staged.txt")
    (root / "main-unstaged.txt").write_text("unstaged bytes\n")
    (root / "main-untracked.txt").write_text("untracked bytes\n")


def _guard_method() -> dict:
    return {
        "id": "INTEGRATION_GUARD",
        "argv": [
            sys.executable,
            "-c",
            (
                "import pathlib,subprocess;"
                "print('cwd='+str(pathlib.Path.cwd()));"
                "print('head='+subprocess.check_output("
                "['git','rev-parse','HEAD'],text=True).strip())"
            ),
        ],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 10,
        "expected_exit_code": 0,
        "stdout_contains": ["cwd=", "head="],
        "stderr_contains": [],
    }


def _integration_failing_guard_method() -> dict:
    return {
        "id": "INTEGRATION_GUARD",
        "argv": [
            sys.executable,
            "-c",
            (
                "import subprocess,sys;"
                "branch=subprocess.check_output("
                "['git','symbolic-ref','--short','HEAD'],text=True).strip();"
                "print('branch='+branch);"
                "sys.exit(1 if 'integration' in branch else 0)"
            ),
        ],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 10,
        "expected_exit_code": 0,
        "stdout_contains": ["branch="],
        "stderr_contains": [],
    }


def _source_change(tree: Path) -> None:
    (tree / "src" / "feature.py").write_text("VALUE = 1\n")


def _conflicting_source_change(tree: Path) -> None:
    (tree / "src" / "double.py").write_text("VALUE = 'source'\n")


def _advance_ref_with_same_tree(root: Path, parent: str, message: str) -> str:
    tree = git(root, "rev-parse", f"{parent}^{{tree}}")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Drift fixture",
        "GIT_AUTHOR_EMAIL": "drift@example.invalid",
        "GIT_COMMITTER_NAME": "Drift fixture",
        "GIT_COMMITTER_EMAIL": "drift@example.invalid",
    }
    commit = subprocess.check_output(
        ["git", "-C", str(root), "commit-tree", tree, "-p", parent, "-m", message],
        text=True,
        env=env,
    ).strip()
    git(root, "update-ref", "refs/heads/main", commit, parent)
    return commit


def _is_target_publication(args: tuple[str, ...]) -> bool:
    return (
        len(args) == 4
        and args[0] == "update-ref"
        and args[1] == "refs/heads/main"
    )


def _show_integration(project, request_id: str = "integrate-1") -> dict:
    reader = WorkTools(Poise(project["config_path"], f"reader-{request_id}"))
    return reader.invoke(request("show", {"queries": [{
        "id": "integration",
        "kind": "integration",
        "task_id": "T1",
        "request_id": request_id,
    }]}))["results"][0]["value"]


def _assert_terminal_contract(
    result: dict,
    source: str,
    repository: Path,
    task_worktree: Path,
    task_branch: str = "tasks/T1",
) -> None:
    assert result["status"] == "integrated"
    assert result["accepted_commit"] == source
    assert result["integration_head"] == result["target_after"]
    assert result["publication"]["status"] == "confirmed"
    assert result["publication"]["target_ref"] == "refs/heads/main"
    assert result["publication"]["commit"] == result["target_after"]
    assert result["cleanup"] == {
        "task_worktree": "removed",
        "integration_worktree": "removed",
        "task_branch": "deleted",
        "integration_branch": "deleted",
        "temporary_backups": "removed",
    }
    assert git(repository, "merge-base", "--is-ancestor", source, result["target_after"]) == ""
    integration_worktree = Path(result["integration_worktree"])
    integration_branch = result["integration_branch"]
    integration_ref = (
        integration_branch
        if integration_branch.startswith("refs/heads/")
        else f"refs/heads/{integration_branch}"
    )
    worktrees = subprocess.check_output(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        text=True,
    )
    assert not task_worktree.exists()
    assert not integration_worktree.exists()
    assert f"worktree {task_worktree}\n" not in worktrees
    assert f"worktree {integration_worktree}\n" not in worktrees
    assert _optional_ref(repository, f"refs/heads/{task_branch}") is None
    assert _optional_ref(repository, integration_ref) is None
    assert not Path(result["temporary_backup_directory"]).exists()


def test_happy_path_uses_child_worktree_runs_guard_and_preserves_dirty_main_checkout(project):
    method = _guard_method()
    tools, source_worktree, source = prepare_completed_task(
        project,
        _source_change,
        methods=[method],
        checks=[method["id"]],
    )
    old_target = git(project["app"], "rev-parse", "refs/heads/main")
    _dirty_main_checkout(project["app"])
    before = _main_checkout_fingerprint(project["app"], old_target)

    result = tools.invoke(request("integrate", integration_input(project, source)))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert git(project["app"], "rev-parse", "refs/heads/main") == result["target_after"]
    assert git(project["app"], "show", f"{result['target_after']}:src/feature.py") == "VALUE = 1"
    assert _main_checkout_fingerprint(project["app"], old_target) == before
    assert not source_worktree.exists()
    assert [item["method"] for item in result["checks"]] == [method["id"]]
    output = Path(result["checks"][0]["stdout"]).read_text()
    assert f"cwd={result['integration_worktree']}" in output
    assert f"head={result['integration_head']}" in output


def test_overlapping_main_checkout_wip_is_not_an_integration_precondition(project):
    tools, source_worktree, source = prepare_completed_task(project, _source_change)
    old_target = git(project["app"], "rev-parse", "refs/heads/main")
    overlap = project["app"] / "src" / "feature.py"
    overlap.write_text("uncommitted operator bytes\n")
    before = _main_checkout_fingerprint(project["app"], old_target)

    result = tools.invoke(request("integrate", integration_input(project, source)))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert _main_checkout_fingerprint(project["app"], old_target) == before
    assert overlap.read_bytes() == b"uncommitted operator bytes\n"


def test_conflict_is_resolved_only_in_persisted_child_worktree_then_checked(project):
    method = _guard_method()
    tools, source_worktree, source = prepare_completed_task(
        project,
        _conflicting_source_change,
        methods=[method],
        checks=[method["id"]],
    )
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "feat: conflicting target")
    payload = integration_input(project, source)
    target_before = git(project["app"], "rev-parse", "refs/heads/main")

    waiting = tools.invoke(request("integrate", payload))

    integration_worktree = Path(waiting["integration_worktree"])
    assert waiting["status"] == "awaiting_resolution"
    assert waiting["accepted_commit"] == source
    assert waiting["observed_target"] == target_before
    assert waiting["conflicts"] == ["src/double.py"]
    assert integration_worktree.is_dir()
    assert _optional_ref(integration_worktree, "MERGE_HEAD") == source
    assert _optional_ref(project["app"], "MERGE_HEAD") is None
    (integration_worktree / "src" / "double.py").write_text("VALUE = 'resolved'\n")

    completed = tools.invoke(request("integrate", {
        **payload,
        "resolutions": [{
            "path": "src/double.py",
            "resolution": "Preserve the accepted behavior with the current target API.",
        }],
    }))

    _assert_terminal_contract(completed, source, project["app"], source_worktree)
    assert git(
        project["app"], "show", f"{completed['target_after']}:src/double.py"
    ) == "VALUE = 'resolved'"
    assert completed["checks"][0]["passed"] is True
    assert not source_worktree.exists()


@pytest.mark.parametrize("drift_count", [1, 2])
def test_target_drift_rebuilds_and_rechecks_before_atomic_publication(
    project, monkeypatch, drift_count
):
    method = _guard_method()
    tools, source_worktree, source = prepare_completed_task(
        project,
        _source_change,
        methods=[method],
        checks=[method["id"]],
    )
    original_run = RuntimeResultIntegration._run
    injected: list[str] = []

    def drift_before_compare_and_swap(self, cwd, *args, env=None):
        if _is_target_publication(args) and len(injected) < drift_count:
            assert args[3] == git(project["app"], "rev-parse", "refs/heads/main")
            current = git(project["app"], "rev-parse", "refs/heads/main")
            injected.append(_advance_ref_with_same_tree(
                project["app"], current, f"test: target drift {len(injected) + 1}"
            ))
        return original_run(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", drift_before_compare_and_swap)

    result = tools.invoke(request("integrate", integration_input(project, source)))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert len(injected) == drift_count
    assert result["publication"]["drift_retries"] == drift_count
    assert len(result["checks"]) == drift_count + 1
    assert all(item["passed"] for item in result["checks"])
    assert git(project["app"], "merge-base", "--is-ancestor", injected[-1], result["target_after"]) == ""


def test_crash_after_atomic_publication_reconciles_without_second_update(project, monkeypatch):
    tools, source_worktree, source = prepare_completed_task(project, _source_change)
    payload = integration_input(project, source)
    original_run = RuntimeResultIntegration._run
    publication_calls = 0
    crashed = False

    def crash_after_update_ref(self, cwd, *args, env=None):
        nonlocal publication_calls, crashed
        if _is_target_publication(args):
            assert args[3] == git(project["app"], "rev-parse", "refs/heads/main")
        receipt = original_run(self, cwd, *args, env=env)
        if _is_target_publication(args):
            publication_calls += 1
            if not crashed and receipt["actual_exit_code"] == 0:
                crashed = True
                raise RuntimeError("injected crash after publication")
        return receipt

    monkeypatch.setattr(RuntimeResultIntegration, "_run", crash_after_update_ref)

    with pytest.raises(RuntimeError, match="after publication"):
        tools.invoke(request("integrate", payload))

    published = git(project["app"], "rev-parse", "refs/heads/main")
    restarted = WorkTools(Poise(project["config_path"], "restarted-integrator"))
    result = restarted.invoke(request("integrate", payload))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert result["target_after"] == published
    assert result["publication"]["recovered"] is True
    assert publication_calls == 1


def test_crash_before_atomic_publication_retries_the_unperformed_compare_and_swap(
    project, monkeypatch
):
    tools, source_worktree, source = prepare_completed_task(project, _source_change)
    payload = integration_input(project, source)
    original_run = RuntimeResultIntegration._run
    attempted = 0
    performed = 0

    def crash_before_first_update_ref(self, cwd, *args, env=None):
        nonlocal attempted, performed
        if _is_target_publication(args):
            assert args[3] == git(project["app"], "rev-parse", "refs/heads/main")
            attempted += 1
            if attempted == 1:
                raise RuntimeError("injected crash before publication")
            performed += 1
        return original_run(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", crash_before_first_update_ref)

    with pytest.raises(RuntimeError, match="before publication"):
        tools.invoke(request("integrate", payload))

    assert git(project["app"], "rev-parse", "refs/heads/main") == payload["expected_target_commit"]
    restarted = WorkTools(Poise(project["config_path"], "restarted-before-publication"))
    result = restarted.invoke(request("integrate", payload))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert attempted == 2
    assert performed == 1


def test_failed_candidate_guard_persists_receipt_and_prevents_publication_and_cleanup(project):
    method = _integration_failing_guard_method()
    tools, source_worktree, source = prepare_completed_task(
        project,
        _source_change,
        methods=[method],
        checks=[method["id"]],
    )
    payload = integration_input(project, source)

    result = tools.invoke(request("integrate", payload))

    assert result["status"] == "blocked"
    assert result["phase"] == "checks_failed"
    assert result["publication"] is None
    assert source_worktree.is_dir()
    assert Path(result["integration_worktree"]).is_dir()
    assert result["checks"][-1]["method"] == method["id"]
    assert result["checks"][-1]["passed"] is False
    assert Path(result["checks"][-1]["stdout"]).is_file()
    assert git(project["app"], "rev-parse", "refs/heads/main") == payload["expected_target_commit"]
    saved = _show_integration(project)
    assert saved["phase"] == result["phase"] == "checks_failed"
    assert saved["integration_head"] == result["integration_head"]
    assert saved["checks"] == result["checks"]
    assert saved["publication"] is result["publication"] is None


@pytest.mark.parametrize("failure_mode", ["before", "after"])
def test_cleanup_retry_finishes_only_owned_resources_and_preserves_foreign_state(
    project, monkeypatch, failure_mode
):
    tools, source_worktree, source = prepare_completed_task(project, _source_change)
    foreign = project["root"] / "foreign-worktree"
    git(project["app"], "worktree", "add", "-b", "foreign/wip", str(foreign), "HEAD")
    (foreign / "foreign.txt").write_text("foreign bytes\n")
    foreign_before = {
        "head": git(foreign, "rev-parse", "HEAD"),
        "status": subprocess.check_output(
            ["git", "-C", str(foreign), "status", "--porcelain=v2", "-z"]
        ),
        "bytes": (foreign / "foreign.txt").read_bytes(),
    }
    protected_backups = {
        "operator": project["root"] / "runtime" / "operator-backups" / "keep.bundle",
        "deliverable": project["root"] / "runtime" / "deliverables" / "keep.bundle",
        "unfinished-recovery": project["root"] / "runtime" / "recovery" / "keep.bundle",
    }
    for kind, backup in protected_backups.items():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(f"{kind} backup\n".encode())
    original_run = RuntimeResultIntegration._run
    failed = False

    cleanup_effects = 0

    def fail_first_integration_worktree_cleanup(self, cwd, *args, env=None):
        nonlocal failed
        if (
            args[:2] == ("worktree", "remove")
            and len(args) >= 3
            and "integration" in args[2]
            and not failed
        ):
            failed = True
            if failure_mode == "after":
                nonlocal cleanup_effects
                receipt = original_run(self, cwd, *args, env=env)
                assert receipt["actual_exit_code"] == 0
                cleanup_effects += 1
                raise RuntimeError("injected crash after owned cleanup")
            return {
                "argv": ["git", "-C", str(cwd), *args],
                "actual_exit_code": 1,
                "stdout": "",
                "stderr": "injected owned cleanup failure",
            }
        return original_run(self, cwd, *args, env=env)

    monkeypatch.setattr(
        RuntimeResultIntegration,
        "_run",
        fail_first_integration_worktree_cleanup,
    )
    payload = integration_input(project, source)

    if failure_mode == "before":
        pending = tools.invoke(request("integrate", payload))
        assert pending["status"] == "cleanup_pending"
        assert pending["publication"]["status"] == "confirmed"
        assert pending["cleanup"]["integration_worktree"] == "blocked"
        assert source_worktree.exists() is (pending["cleanup"]["task_worktree"] != "removed")
    else:
        with pytest.raises(RuntimeError, match="after owned cleanup"):
            tools.invoke(request("integrate", payload))

    saved = _show_integration(project)
    assert saved["publication"]["status"] == "confirmed"
    assert saved["cleanup"] != {
        "task_worktree": "removed",
        "integration_worktree": "removed",
        "task_branch": "deleted",
        "integration_branch": "deleted",
        "temporary_backups": "removed",
    }
    temporary_directory = Path(saved["temporary_backup_directory"])
    assert temporary_directory.is_relative_to(project["root"] / "runtime")
    temporary_directory.mkdir(parents=True, exist_ok=True)
    temporary_backup = temporary_directory / "owned-retry.bundle"
    temporary_backup.write_bytes(b"task-scoped temporary backup\n")

    restarted = WorkTools(Poise(project["config_path"], f"cleanup-restart-{failure_mode}"))
    completed = restarted.invoke(request("integrate", payload))

    _assert_terminal_contract(completed, source, project["app"], source_worktree)
    assert not temporary_backup.exists()
    assert foreign.is_dir()
    assert git(foreign, "rev-parse", "HEAD") == foreign_before["head"]
    assert subprocess.check_output(
        ["git", "-C", str(foreign), "status", "--porcelain=v2", "-z"]
    ) == foreign_before["status"]
    assert (foreign / "foreign.txt").read_bytes() == foreign_before["bytes"]
    for kind, backup in protected_backups.items():
        assert backup.read_bytes() == f"{kind} backup\n".encode()
    if failure_mode == "after":
        assert cleanup_effects == 1


def test_terminal_no_op_is_proved_and_cleanup_is_replayable(project):
    tools, source_worktree, source = prepare_completed_task(project, _source_change)
    old_target = git(project["app"], "rev-parse", "refs/heads/main")
    git(project["app"], "update-ref", "refs/heads/main", source, old_target)
    payload = integration_input(project, source)

    result = tools.invoke(request("integrate", payload))

    _assert_terminal_contract(result, source, project["app"], source_worktree)
    assert result["publication"]["no_op"] is True
    assert result["publication"]["observed_target"] == source
    replay = tools.invoke(request("integrate", payload))
    assert replay["status"] == "integrated"
    assert replay["replayed"] is True
    assert replay["publication"] == result["publication"]


def test_show_exposes_exact_persisted_integration_contract_during_conflict(project):
    tools, _, source = prepare_completed_task(project, _conflicting_source_change)
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "feat: conflicting target")
    payload = integration_input(project, source, request_id="persisted-contract")
    waiting = tools.invoke(request("integrate", payload))

    shown = _show_integration(project, "persisted-contract")

    for key in (
        "accepted_commit",
        "integration_branch",
        "integration_worktree",
        "phase",
        "observed_target",
        "integration_head",
        "conflicts",
        "resolutions",
        "checks",
        "publication",
        "temporary_backups",
        "cleanup",
        "history",
    ):
        assert key in shown
    assert shown["status"] == waiting["status"] == "awaiting_resolution"
    for key in (
        "accepted_commit",
        "integration_branch",
        "integration_worktree",
        "phase",
        "observed_target",
        "integration_head",
        "conflicts",
        "resolutions",
        "checks",
        "publication",
        "temporary_backups",
        "cleanup",
        "history",
    ):
        assert shown[key] == waiting[key]
    assert shown["accepted_commit"] == source
    assert shown["phase"] == "awaiting_resolution"
    assert shown["observed_target"] == payload["expected_target_commit"]
    assert shown["conflicts"] == ["src/double.py"]
    assert shown["resolutions"] == []
    assert shown["checks"] == []
    assert shown["publication"] is None
