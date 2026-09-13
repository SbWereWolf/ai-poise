import json
from pathlib import Path
import sqlite3
import subprocess

import pytest
from batch.helpers import request
from conftest import Poise, WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.modules.hook_transport.domain import BoundSourceRoute
from runner.test_runner_paths import edit, result, setup_project


def _cleaned_verified(project, *, foreign_claim=False, integrate=True):
    executor = setup_project(project, "development")
    process = json.loads(
        (project["root"] / "config/processes/development.json").read_text(encoding="utf-8")
    )
    task = project["task"]
    task["stage_contracts"] = [{
        "stage_id": stage["id"],
        "allowed_paths": list(stage["allowed_paths"]),
        "entry_requirements": [],
        "exit_requirements": [],
    } for stage in process["stages"]]
    write_json(project["task_path"], task)
    context = executor.bootstrap(task_file=project["task_path"])
    edit(context, "development", "verified recovery source\n")
    result(context, {})
    verified = executor.verify()
    handoff = WorkTools(executor).invoke(request("handoff", {
        "request_id": "verified-before-cleanup",
        "reason": "Preserve the exact verified source.",
        "result": None,
        "commit_message": "test: preserve verified recovery source",
        "artifact_paths": [],
    }))

    root = project["app"]
    worktree = Path(context["worktree"])
    branch = executor.task_queries.record(context["task"])["branch"]
    if integrate:
        git(root, "merge", "--ff-only", branch)
    if foreign_claim:
        foreign = WorkPoise(project["config_path"], "FOREIGN")
        WorkTools(foreign).invoke(request("bootstrap", {
            "task": {"id": context["task"]},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        }))
    else:
        foreign = None
    git(root, "worktree", "remove", str(worktree))
    git(root, "branch", "-D", branch)
    return executor, foreign, context, verified, handoff, root, worktree, branch


def _state_snapshot(runtime, task_id):
    with sqlite3.connect(runtime.store.path) as connection:
        return {
            table: connection.execute(
                f'SELECT * FROM "{table}" WHERE task_id=? ORDER BY rowid', (task_id,)
            ).fetchall()
            for table in ("task_execution", "handoffs")
        } | {
            "tasks": connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchall(),
            "sessions": connection.execute(
                "SELECT * FROM sessions WHERE task_id=? ORDER BY rowid", (task_id,)
            ).fetchall(),
        }


def test_recovers_exact_integrated_verified_worktree_for_reviewer(project):
    executor, _, context, verified, handoff, _, worktree, branch = _cleaned_verified(project)
    before = executor.task_queries.record(context["task"])

    recovered = WorkTools(Poise(project["config_path"], "RECOVERY")).invoke(
        request("recover_missing_worktree", {
            "task_id": context["task"],
            "reason": "Restore the integrated verified source for its reviewer.",
        })
    )
    recovered.pop("interaction")

    assert recovered == {
        "status": "recovered",
        "task": context["task"],
        "worktree": str(worktree),
        "branch": branch,
        "commit": handoff["commit"],
        "tree": verified["verified_tree"],
        "replayed": False,
    }
    after = executor.task_queries.record(context["task"])
    assert after["claimed_by"] == before["claimed_by"] is None
    assert after["_version"] == before["_version"]
    assert git(worktree, "rev-parse", "HEAD") == handoff["commit"]
    assert git(worktree, "rev-parse", "HEAD^{tree}") == verified["verified_tree"]
    assert git(worktree, "status", "--porcelain") == ""

    replay = WorkTools(Poise(project["config_path"], "RECOVERY")).invoke(
        request("recover_missing_worktree", {
            "task_id": context["task"],
            "reason": "Restore the integrated verified source for its reviewer.",
        })
    )
    replay.pop("interaction")
    assert replay == {**recovered, "replayed": True}

    reviewer = WorkTools(WorkPoise(project["config_path"], "REVIEWER")).invoke(
        request("bootstrap", {
            "task": {"id": context["task"]},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        })
    )
    assert reviewer["status"] == "verified"
    assert reviewer["stage"] == verified["stage"]
    assert reviewer["iteration"] == verified["iteration"]


def test_recovers_without_changing_foreign_claim_or_task_records(project):
    executor, foreign, context, verified, _, _, worktree, _ = _cleaned_verified(
        project, foreign_claim=True
    )
    recovery = Poise(project["config_path"], "RECOVERY")
    before = _state_snapshot(recovery, context["task"])

    recovered = recovery.recover_missing_worktree(
        context["task"], "Restore source while preserving the foreign claim."
    )

    assert recovered["replayed"] is False
    assert _state_snapshot(recovery, context["task"]) == before
    assert executor.task_queries.record(context["task"])["claimed_by"] == foreign.session
    released = WorkTools(foreign).invoke(request("handoff", {
        "request_id": "release-after-worktree-recovery",
        "reason": "Recovered source can now be transferred.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    assert released["status"] == "handed_off"
    reviewer = WorkTools(WorkPoise(project["config_path"], "REVIEWER-FOREIGN")).invoke(
        request("bootstrap", {
            "task": {"id": context["task"]},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        })
    )
    assert (reviewer["status"], reviewer["stage"], reviewer["iteration"]) == (
        "verified", verified["stage"], verified["iteration"]
    )
    assert worktree.is_dir()


def test_rejects_independent_repository_at_saved_path(project):
    _, _, context, _, handoff, root, worktree, branch = _cleaned_verified(project)
    subprocess.check_call(["git", "clone", "--quiet", str(root), str(worktree)])
    git(worktree, "checkout", "-b", branch, handoff["commit"])

    with pytest.raises(Exception, match="not a worktree of the configured repository"):
        Poise(project["config_path"], "RECOVERY").recover_missing_worktree(
            context["task"], "Reject an independent repository."
        )


@pytest.mark.parametrize("fault", ["tree", "audit"])
def test_rolls_back_only_invocation_created_git_resources(project, monkeypatch, fault):
    _, _, context, _, _, root, worktree, branch = _cleaned_verified(project)
    recovery = Poise(project["config_path"], "RECOVERY")
    if fault == "tree":
        monkeypatch.setattr(recovery, "_tree", lambda path: "0" * 40)
        expected = "does not match the verified source"
    else:
        monkeypatch.setattr(
            recovery.store, "event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit fault"))
        )
        expected = "audit fault"

    with pytest.raises(Exception, match=expected):
        recovery.recover_missing_worktree(context["task"], "Inject a recovery fault.")

    assert not worktree.exists()
    assert subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", f"refs/heads/{branch}"],
        check=False,
        capture_output=True,
    ).returncode != 0


def test_rejects_unintegrated_commit_and_mismatched_verified_tree(project, monkeypatch):
    _, _, context, _, _, root, worktree, _ = _cleaned_verified(project, integrate=False)
    recovery = Poise(project["config_path"], "RECOVERY")
    with pytest.raises(Exception, match="not integrated"):
        recovery.recover_missing_worktree(context["task"], "Reject unintegrated source.")
    assert not worktree.exists()

    data = recovery.task_queries.record(context["task"])
    git(root, "merge", "--ff-only", data["last_report"]["commit"])
    data["last_report"] = {**data["last_report"], "verified_tree": "0" * 40}
    monkeypatch.setattr(recovery.task_queries, "record", lambda task_id: data)
    with pytest.raises(Exception, match="does not match"):
        recovery.recover_missing_worktree(context["task"], "Reject a mismatched tree.")


def test_missing_worktree_recovery_is_installation_owned_and_documented():
    route = BoundSourceRoute.decide(
        "recover_missing_worktree",
        None,
        {"id": "CURRENT", "status": "verified"},
        None,
    )
    assert (route.source, route.task_id) == ("installation", None)
    text = (Path(__file__).resolve().parents[2] / "docs/workflows/batch-work.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "`recover_missing_worktree`",
        "не меняет ownership, Task version, stage, iteration, result или handoff",
        "предком текущего настроенного `base_ref`",
        "совпасть с сохранённым `verified_tree`",
        "`replayed: true`",
    ):
        assert phrase in text
