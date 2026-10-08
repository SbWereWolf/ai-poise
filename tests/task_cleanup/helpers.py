from __future__ import annotations

from pathlib import Path

from batch.helpers import request
from conftest import git
from result_integration.helpers import prepare_completed_task


def prepare_cancelled_task(project, *, task_id="T1", durable_artifact=False):
    tools, worktree, commit = prepare_completed_task(
        project,
        lambda root: (root / "src" / "cancelled.py").write_text(
            "VALUE = 'cancelled task commit'\n", encoding="utf-8"
        ),
        task_id=task_id,
        accept=False,
        artifact_files=(
            {"durable-before-cleanup.txt": "durable task evidence\n"}
            if durable_artifact else None
        ),
    )
    if durable_artifact:
        import hashlib
        record = tools.runtime.store.artifact_records(task_id)[0]
        destination = project["root"] / "delivered-evidence.txt"
        value = tools.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))["results"][0]["value"]
        tools.invoke(request("delivery", {
            "action": "agree", "request_id": "deliver-before-cancel", "task_id": task_id,
            "expected_version": value["version"],
            "authorization": "The fixture customer explicitly retains this evidence at its permanent path.",
            "declaration": {"disposition": "deliver", "outputs": [{
                "id": "retained-evidence", "kind": "file", "source": record["path"],
                "destination": str(destination),
                "digest": hashlib.sha256(b"durable task evidence\n").hexdigest(),
            }]},
        }))
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
