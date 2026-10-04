"""Exact work preservation and durable-effect oracles at the public Task boundary."""

from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from runtime_services.restart_auto_support import assert_recovery, bootstrap, launch, prepared, snapshot
from batch.helpers import result, verify


@pytest.mark.parametrize("dirty", ["tracked", "new", "staged"])
def test_uncommitted_task_work_refuses_before_any_rewind(project, dirty):
    case = prepared(project)
    path = case["root"] / "src" / ("saved.txt" if dirty == "tracked" else "new.txt")
    path.write_bytes(b"uncommitted work\n")
    if dirty == "staged":
        git(case["root"], "add", "src/new.txt")
    before = snapshot(case)
    with pytest.raises(PoiseError, match="commit|uncommitted|preserv|dirty"):
        case["client"].invoke(request("advance", {
            "request_id": "dirty", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before


def test_changed_request_target_cannot_reuse_confirmed_effects(project):
    case = prepared(project)
    launch(case, target="implementation")
    before = snapshot(case)
    with pytest.raises(PoiseError, match="request|identity|intent"):
        case["client"].invoke(request("advance", {
            "request_id": "replay", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before


def test_recovery_reference_keeps_tracked_and_new_saved_bytes(project):
    case = prepared(project)
    response = launch(case, target="implementation")
    assert_recovery(case, response)
    ref = response["replay"]["recovery_ref"]
    assert git(case["root"], "show", f"{ref}:src/double.py") == "def double(n):\n    return n * 2"
    assert git(case["root"], "show", f"{ref}:src/saved.txt") == "saved work"
    assert not (case["root"] / "src" / "saved.txt").exists()
    assert git(case["project"]["app"], "status", "--porcelain") == ""
    assert case["client"].runtime.task_queries.record("T1")["id"] == "T1"
    assert case["client"].runtime.task_queries.record("T1")["sprint_id"] is None


def test_post_checkout_interruption_resumes_without_lost_saved_work(project, monkeypatch):
    case = prepared(project)
    runtime = case["client"].runtime
    real_git = runtime._git
    interrupted = []

    def git_with_interruption(cwd, *args, **kwargs):
        answer = real_git(cwd, *args, **kwargs)
        if (not interrupted and Path(cwd) == case["root"]
                and any(value in ("reset", "checkout", "switch") for value in args)
                and git(case["root"], "rev-parse", "HEAD") == case["commits"][0]):
            interrupted.append(True)
            raise PoiseError("test-owned interruption after historical checkout")
        return answer

    monkeypatch.setattr(runtime, "_git", git_with_interruption)
    with pytest.raises(PoiseError, match="test-owned interruption"):
        runtime_client = case["client"]
        runtime_client.invoke(request("advance", {
            "request_id": "replay", "task_id": "T1", "target_stage": "code_review",
        }))
    assert interrupted == [True]
    assert git(case["root"], "rev-parse", "HEAD") == case["commits"][0]
    monkeypatch.setattr(runtime, "_git", real_git)
    resumed = launch(case, target="code_review")
    assert resumed["status"] == "progression_target_reached"
    assert case["log"].read_text().splitlines() == case["commits"]
    assert_recovery(case, resumed)


def test_replay_preserves_history_and_normal_verify_handoff(project):
    case = prepared(project)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    context = bootstrap(case["client"], {"id": "T1"})
    assert context["stage"] == "code_review"
    delivered = verify(case["client"], result(context))
    assert delivered["status"] == "verified"
    with case["client"].runtime.store.transaction() as database:
        preserved = [tuple(row) for row in database.execute(
            "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
            ("T1",),
        )]
    assert preserved[:3] == case["historical_results"]
    released = case["client"].invoke(request("handoff", {
        "request_id": "ordinary-after-replay", "reason": "Normal stage handoff after replay.",
        "result": None, "commit_message": None, "artifact_paths": [],
    }))
    assert released["status"] == "handed_off"
    assert released["verified"] is True
    assert case["client"].runtime.task_queries.record("T1")["claimed_by"] is None
    assert git(case["root"], "rev-parse", response["replay"]["recovery_ref"]) == case["saved"]
