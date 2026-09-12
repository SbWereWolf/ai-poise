from pathlib import Path
import subprocess

import pytest

from batch.helpers import request
from conftest import git
from poise.modules.foundation.errors import PoiseError

from .helpers import branch_exists, cleanup_input, prepare_cancelled_task


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
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    main_before = git(project["app"], "rev-parse", "HEAD")
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

    replay = tools.invoke(packet)
    assert replay["status"] == "cleanup_complete" and replay["replayed"] is True
    assert replay["history"] == completed["history"]


def test_preserve_creates_verified_durable_bundle_before_ref_deletion(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    packet = request("cleanup", cleanup_input(commit, kind="preserved"))

    completed = tools.invoke(packet)

    assert completed["status"] == "cleanup_complete"
    assert completed["disposition"]["kind"] == "preserved"
    bundle = Path(completed["disposition"]["bundle_path"])
    assert bundle.is_file()
    assert git(project["app"], "bundle", "verify", str(bundle)) == ""
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
    shown = tools.invoke(request("show", {"queries": [{
        "id": "cleanup",
        "kind": "task_cleanup",
        "task_id": "T1",
        "request_id": "cleanup-1",
    }]}))["results"][0]["value"]
    assert shown["status"] == "cleanup_blocked"
    assert shown["history"] == blocked["history"]

    git(project["app"], "worktree", "unlock", str(worktree))
    completed = tools.invoke(packet)
    assert completed["status"] == "cleanup_complete"
    assert not worktree.exists() and not branch_exists(project)
    assert any(item.get("event") == "worktree_cleanup_blocked" for item in completed["history"])


def test_cleanup_rejects_commit_or_request_identity_drift_without_effect(project):
    tools, worktree, commit, _ = prepare_cancelled_task(project)
    original = cleanup_input(commit)
    tools.invoke(request("cleanup", original))

    changed = cleanup_input(commit, kind="preserved")
    with pytest.raises(PoiseError, match="immutable|identity|request"):
        tools.invoke(request("cleanup", changed))
    assert not worktree.exists()
