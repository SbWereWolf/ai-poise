from __future__ import annotations

from pathlib import Path

from batch.helpers import request
from conftest import git
from result_integration.helpers import prepare_completed_task


def prepare_cancelled_task(project, *, task_id="T1"):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / "cancelled.py").write_text(
            "VALUE = 'cancelled task commit'\n", encoding="utf-8"
        ),
        task_id=task_id,
        accept=False,
    )
    cancelled = tools.invoke(request("cancel", {"reason": "User cancelled this exact Task."}))
    return tools, Path(worktree), commit, cancelled


def cleanup_input(commit, *, request_id="cleanup-1", kind="discard_authorized"):
    return {
        "request_id": request_id,
        "task_id": "T1",
        "commit_disposition": {"kind": kind, "expected_commit": commit},
        "authorization": "User explicitly authorized this exact commit disposition.",
    }


def branch_exists(project, name="tasks/T1"):
    return git(project["app"], "branch", "--list", name) != ""
