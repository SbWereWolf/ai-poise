import importlib.util
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request
from conftest import WorkPoise as Poise, git
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from result_integration.helpers import prepare_completed_task

from .helpers import branch_exists, cleanup_input, prepare_cancelled_task


def cleanup_infrastructure():
    spec = importlib.util.find_spec("poise.infrastructure.task_cleanup")
    assert spec is not None, "RuntimeTaskResourceCleanup is not implemented"
    from poise.infrastructure import task_cleanup
    return task_cleanup


def test_terminal_cleanup_requires_explicit_commit_disposition(project):
    tools, worktree, commit, cancelled = prepare_cancelled_task(project)

    assert cancelled["status"] == "cancelled"
    assert cancelled["cleanup"] == {
        "status": "disposition_required",
        "task": "T1",
        "commit": commit,
        "remaining_resources": [
            {"kind": "worktree", "path": str(worktree)},
            {"kind": "branch", "name": "tasks/T1", "commit": commit},
        ],
        "blocker": {
            "reason": "commit_disposition_required",
            "recovery": "Invoke cleanup with an explicit preserved or discard_authorized disposition.",
        },
    }
    assert worktree.is_dir() and branch_exists(project)
    ancestor = subprocess.run(
        ["git", "-C", str(project["app"]), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ancestor.returncode == 1

    missing = cleanup_input(commit)
    missing.pop("commit_disposition")
    with pytest.raises(PoiseError, match="commit.disposition|exact.*fields"):
        tools.invoke(request("cleanup", missing))
    assert worktree.is_dir() and branch_exists(project)


def test_explicit_discard_removes_only_exact_task_resources_and_replays(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project, durable_artifact=True)
    main_before = git(project["app"], "rev-parse", "HEAD")
    history_before = tools.runtime.task_queries.history("T1")
    artifacts_before = tools.runtime.store.artifact_records("T1")
    foreign = project["app"].parent / "foreign-worktree"
    git(project["app"], "branch", "foreign-owned")
    git(project["app"], "worktree", "add", str(foreign), "foreign-owned")
    operator_backup = project["root"] / "state" / "backups" / "operator.sqlite"
    operator_backup.parent.mkdir(parents=True, exist_ok=True)
    operator_backup.write_text("operator-owned\n", encoding="utf-8")
    packet = request("cleanup", cleanup_input(commit))

    completed = tools.invoke(packet)

    assert completed["status"] == "cleanup_complete"
    assert completed["disposition"]["kind"] == "discard_authorized"
    assert completed["remaining_resources"] == []
    assert not worktree.exists() and not branch_exists(project)
    assert foreign.is_dir()
    assert git(project["app"], "branch", "--list", "foreign-owned")
    assert operator_backup.read_text(encoding="utf-8") == "operator-owned\n"
    assert git(project["app"], "rev-parse", "HEAD") == main_before
    assert tools.runtime.task_queries.history("T1")[:len(history_before)] == history_before
    durable = next(item for item in artifacts_before if item["path"].endswith("durable-before-cleanup.txt"))
    assert not Path(durable["path"]).exists()
    assert (project["root"] / "delivered-evidence.txt").read_bytes() == b"durable task evidence\n"

    replay = tools.invoke(packet)
    assert replay["status"] == "cleanup_complete" and replay["replayed"] is True
    assert replay["history"] == completed["history"]

    changed = request("cleanup", cleanup_input(commit, kind="preserved"))
    with pytest.raises(PoiseError, match="immutable|identity|request"):
        tools.invoke(changed)


def test_preserve_creates_verified_pending_bundle_before_ref_deletion(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    packet = request("cleanup", cleanup_input(commit, kind="preserved"))

    completed = tools.invoke(packet)

    assert completed["status"] == "cleanup_complete"
    assert completed["disposition"]["kind"] == "preserved"
    bundle = Path(completed["disposition"]["bundle_path"])
    assert bundle.is_file()
    verification = subprocess.run(
        ["git", "-C", str(project["app"]), "bundle", "verify", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stderr
    assert "is okay" in verification.stderr
    assert commit in git(project["app"], "bundle", "list-heads", str(bundle))
    assert not worktree.exists() and not branch_exists(project)
    task_artifacts = tools.runtime.store.artifact_records("T1")
    assert any(item["path"] == str(bundle) for item in task_artifacts)


def test_cleanup_failure_is_persisted_actionable_and_idempotently_resumed(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    git(project["app"], "worktree", "lock", str(worktree))
    packet = request("cleanup", cleanup_input(commit))

    blocked = tools.invoke(packet)

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["resource"]["kind"] == "worktree"
    assert blocked["blocker"]["recovery"]
    assert blocked["remaining_resources"]
    assert worktree.exists() and branch_exists(project)
    resumer = WorkTools(Poise(project["config_path"], "cleanup-resumer"))
    shown = resumer.invoke(request("show", {"queries": [{
        "id": "cleanup",
        "kind": "task_cleanup",
        "task_id": "T1",
        "request_id": "cleanup-1",
    }]}))["results"][0]["value"]
    assert shown["status"] == "cleanup_blocked"
    assert shown["history"] == blocked["history"]

    git(project["app"], "worktree", "unlock", str(worktree))
    completed = resumer.invoke(packet)
    assert completed["status"] == "cleanup_complete"
    assert not worktree.exists() and not branch_exists(project)
    assert any(item.get("event") == "worktree_cleanup_blocked" for item in completed["history"])


def test_partial_cleanup_failure_is_reloaded_and_resumed_after_process_loss(project, monkeypatch):
    module = cleanup_infrastructure()
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    packet = request("cleanup", cleanup_input(commit))
    original_run = module.RuntimeTaskResourceCleanup._run
    failed = False

    def fail_first_branch_delete(self, cwd, *args, env=None):
        nonlocal failed
        if not failed and args[:3] == ("update-ref", "-d", "refs/heads/tasks/T1"):
            failed = True
            return {
                "argv": ["git", "-C", str(cwd), *args],
                "actual_exit_code": 1,
                "stdout": "",
                "stderr": "injected exact-ref deletion failure",
            }
        return original_run(self, cwd, *args, env=env)

    monkeypatch.setattr(module.RuntimeTaskResourceCleanup, "_run", fail_first_branch_delete)
    blocked = tools.invoke(packet)

    assert blocked["status"] == "cleanup_blocked"
    assert not worktree.exists() and branch_exists(project)
    assert blocked["remaining_resources"] == [
        {"kind": "branch", "name": "tasks/T1", "commit": commit}
    ]
    monkeypatch.setattr(module.RuntimeTaskResourceCleanup, "_run", original_run)

    resumer = WorkTools(Poise(project["config_path"], "cleanup-after-process-loss"))
    completed = resumer.invoke(packet)
    assert completed["status"] == "cleanup_complete"
    assert not branch_exists(project)
    assert any(
        item.get("event") == "branch_cleanup_blocked"
        and "injected exact-ref deletion failure" in item["receipt"]["stderr"]
        for item in completed["history"]
    )


def test_cleanup_blocks_when_the_registered_branch_commit_has_drifted(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    main_before = git(project["app"], "rev-parse", "HEAD")
    (worktree / "src" / "drift.py").write_text("DRIFT = True\n", encoding="utf-8")
    git(worktree, "add", "src/drift.py")
    git(worktree, "commit", "-m", "test: move registered task branch")
    drifted = git(worktree, "rev-parse", "HEAD")

    blocked = tools.invoke(request("cleanup", cleanup_input(commit)))

    assert blocked["status"] == "cleanup_blocked"
    assert blocked["blocker"]["reason"] == "owned_resource_changed"
    assert blocked["blocker"]["resource"]["kind"] == "branch"
    assert blocked["blocker"]["expected_commit"] == commit
    assert blocked["blocker"]["actual_commit"] == drifted
    assert worktree.is_dir() and branch_exists(project)
    assert git(project["app"], "rev-parse", "HEAD") == main_before


def test_dirty_standalone_worktree_remains_blocked_after_disposition_replay(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / "cancelled.py").write_text(
            "VALUE = 'cancelled task commit'\n", encoding="utf-8"
        ),
        accept=False,
    )
    marker = Path(worktree) / "unfinished.txt"
    marker.write_text("uncommitted user work\n", encoding="utf-8")

    cancelled = tools.invoke(request("cancel", {"reason": "User cancelled this dirty Task."}))
    assert cancelled["cleanup"]["status"] == "cleanup_blocked"
    assert cancelled["cleanup"]["blocker"]["reason"] == "dirty_worktree_requires_decision"

    replay = tools.invoke(request("cleanup", cleanup_input(commit)))
    assert replay["status"] == "cleanup_blocked"
    assert replay["blocker"]["reason"] == "dirty_worktree_requires_decision"
    assert marker.read_text(encoding="utf-8") == "uncommitted user work\n"
    assert Path(worktree).is_dir() and branch_exists(project)


def test_registered_temporary_resources_flow_through_public_cleanup(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / "temporary.py").write_text(
            "VALUE = 'temporary resource owner'\n", encoding="utf-8"
        ),
        accept=False,
    )
    adapter = tools.runtime.cleanup_tools.adapter
    temporary = adapter.resource_root("T1", "temporary") / "merge.index"
    backup = adapter.resource_root("T1", "temporary_backup") / "recovery.bundle.tmp"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text("temporary index\n", encoding="utf-8")
    backup.write_text("temporary backup\n", encoding="utf-8")
    adapter.register("T1", "temporary", temporary)
    adapter.register("T1", "temporary_backup", backup)

    cancelled = tools.invoke(request("cancel", {"reason": "User cancelled this exact Task."}))
    kinds = [item["kind"] for item in cancelled["cleanup"]["remaining_resources"]]
    assert kinds == ["worktree", "branch", "temporary", "temporary_backup"]

    resumer = WorkTools(Poise(project["config_path"], "temporary-cleanup-resumer"))
    completed = resumer.invoke(request("cleanup", cleanup_input(commit)))
    assert completed["status"] == "cleanup_complete"
    assert not temporary.exists() and not backup.exists()
    assert not Path(worktree).exists() and not branch_exists(project)


def test_explicit_cleanup_initializes_a_preexisting_terminal_task(project):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / "legacy.py").write_text(
            "VALUE = 'legacy terminal task'\n", encoding="utf-8"
        ),
        accept=False,
    )
    with tools.runtime.store.unit_of_work() as uow:
        task = uow.tasks.load("T1")
        change = task.cancel(tools.runtime.session, "Legacy cancellation without cleanup state.")
        uow.tasks.save(change, task.state.version)
    tools.runtime.store.bind(tools.runtime.session, None)
    assert tools.runtime.task_queries.record("T1")["pending"] is None

    completed = tools.invoke(request("cleanup", cleanup_input(commit)))

    assert completed["status"] == "cleanup_complete"
    assert not Path(worktree).exists() and not branch_exists(project)
