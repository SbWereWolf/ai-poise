"""Public replay preserves real Sprint links and nonempty workflow history."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from runner.helpers import decision, finding, inspect, resolution
from runner.test_runner_paths import edit, result as stage_result, setup_project
from runtime_services.restart_auto_support import (
    accepted_visit, assert_projection, launch, prepared,
)
from runtime_services.test_restart_handoff_lifecycle import feedback_history
from runtime_services.test_task_restart import restart


def test_public_replay_preserves_published_sprint_membership_and_dependency(project):
    case = prepared(project, sprint=True)
    runtime = case["client"].runtime
    baseline = case["sprint_before_restart"]
    assert baseline["members"] == [("T1", "S"), ("T2", "S")]
    assert baseline["dependencies"] == [("S", "T1", "T2", "completion")]
    before = deepcopy(runtime.task_queries.record("T1"))
    assert before["sprint_id"] == "S"

    response = launch(case, target="code_review")

    assert response["status"] == "progression_target_reached"
    assert_projection(case, response)
    assert case["log"].read_text().splitlines() == case["commits"]
    after = runtime.task_queries.record("T1")
    assert (after["id"], after["sprint_id"], after["worktree"]) == (
        "T1", "S", before["worktree"],
    )
    with runtime.store.transaction() as database:
        assert [tuple(row) for row in database.execute(
            "SELECT task_id,sprint_id FROM sprint_members ORDER BY task_id")] == baseline["members"]
        assert [tuple(row) for row in database.execute(
            "SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies"
        )] == baseline["dependencies"]
    assert runtime.task_queries.record("T2") == baseline["peer"]


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_public_replay_retains_nonempty_finding_resolution_history(project, outcome):
    history_runtime = setup_project(project, "development")
    context = history_runtime.bootstrap(task_file=project["task_path"])
    root = Path(context["worktree"])
    visits = []
    for stage in ("draft", "audit", "amend", "follow_up"):
        assert context["stage"] == stage
        if stage in ("draft", "amend"):
            edit(context, "development", f"{stage} accepted bytes\n")
        work = {
            "draft": {},
            "audit": inspect([finding("F1")]),
            "amend": {"resolutions": [resolution("R1", "F1")]},
            "follow_up": inspect(decisions=[decision("R1", outcome)]),
        }[stage]
        stage_result(context, work)
        assert history_runtime.verify()["status"] == "verified"
        visits.append(accepted_visit(WorkTools(history_runtime), root, stage))
        if stage != "follow_up":
            context = history_runtime.bootstrap(decision="continue")
    tools = WorkTools(WorkPoise(project["config_path"], "S1"))
    runtime = tools.runtime
    history = feedback_history(runtime, "T1")
    assert any(layer["feedback"]["findings"] for layer in history)
    assert any(layer["feedback"]["resolutions"] for layer in history)
    feedback = deepcopy(runtime.task_commands.workflow_context("T1")["feedback"])
    assert bool(feedback["open_findings"]) == (outcome == "rejected")
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    (root / "src/saved.txt").write_bytes(b"Preserved before replay\n")
    git(root, "add", "src/saved.txt")
    git(root, "commit", "-m", "Preserve owned work before restart")
    saved = git(root, "rev-parse", "HEAD")
    born = restart(tools, "T1", runtime.task_queries.record("T1")["version"])
    ready = tools.invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": "history-ready",
        "expected_revision": born["revision"],
    }))
    assert ready["ready"] is True
    tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert feedback_history(runtime, "T1") == history

    response = tools.invoke(request("advance", {
        "request_id": "history-replay", "task_id": "T1",
    }))

    proof = response["replay"]
    assert proof["passed"] == visits
    assert proof["start_commit"] == saved
    assert git(root, "rev-parse", proof["recovery_ref"]) == saved
    assert git(root, "show", f"{proof['recovery_ref']}:src/saved.txt") == "Preserved before replay"
    assert git(root, "rev-parse", "HEAD") == visits[-1]["commit"]
    assert git(root, "symbolic-ref", "--short", "HEAD") == branch
    assert git(root, "status", "--porcelain") == ""
    assert feedback_history(runtime, "T1")[:len(history)] == history
    assert runtime.task_commands.workflow_context("T1")["feedback"] == feedback
    record = runtime.task_queries.record("T1")
    assert record["id"] == "T1" and record["worktree"] == str(root)
    if outcome == "accepted":
        assert response["status"] == "progression_stopped"
        assert proof["reason"] == "task_acceptance_required"
        assert proof["stopped_at"] == "follow_up"
    else:
        assert response["status"] == "progression_work_required"
        assert proof["reason"] == "work_required"
        assert proof["stopped_at"] == "amend"
    assert record["status"] != "completed"
