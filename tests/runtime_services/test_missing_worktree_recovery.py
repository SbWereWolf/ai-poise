import json
from pathlib import Path

from batch.helpers import request
from conftest import Poise, WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.modules.hook_transport.domain import BoundSourceRoute
from runner.test_runner_paths import edit, result, setup_project


def test_recovers_exact_integrated_verified_worktree_for_reviewer(project):
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
    git(root, "merge", "--ff-only", branch)
    git(root, "worktree", "remove", str(worktree))
    git(root, "branch", "-d", branch)
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
