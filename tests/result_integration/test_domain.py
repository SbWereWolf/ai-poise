import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.result_integration.domain import IntegrationIntent, IntegrationRun


def intent():
    return IntegrationIntent.parse({
        "request_id": "integrate-1",
        "task_id": "T1",
        "expected_source_commit": "a" * 40,
        "expected_target_commit": "b" * 40,
        "authorization": "The user accepted the completed task result.",
        "resolutions": [],
    })


def test_run_records_conflict_and_requires_exact_resolution_set():
    run = IntegrationRun.new(
        intent(),
        "refs/heads/integration/T1-example",
        "/state/worktrees/T1-integration-example",
        "/state/runtime/session/result-integration/T1/example",
    ).begin_candidate("b" * 40)
    run = run.await_resolution(["src/a.py"], {"exit_code": 1})

    with pytest.raises(DomainError, match="every observed conflict"):
        run.continue_with([])

    continued = run.continue_with([
        {"path": "src/a.py", "resolution": "Combined the accepted source and target behavior."}
    ])
    assert continued.status == "running"
    assert continued.resolutions[0]["path"] == "src/a.py"


def test_cleanup_progress_is_monotonic_and_replayable():
    run = IntegrationRun.new(
        intent(),
        "refs/heads/integration/T1-example",
        "/state/worktrees/T1-integration-example",
        "/state/runtime/session/result-integration/T1/example",
    ).begin_candidate("b" * 40)
    run = run.candidate_ready("c" * 40, {"exit_code": 0})
    run = run.checks_recorded([])
    run = run.publication_confirmed(
        "refs/heads/main", {"exit_code": 0}, no_op=False, recovered=False
    )
    pending = run.cleanup_blocked("integration_worktree", {"reason": "locked"})
    assert pending.status == "cleanup_pending"
    assert pending.target_after == "c" * 40

    complete = pending
    for component, outcome in (
        ("integration_worktree", "removed"),
        ("task_worktree", "removed"),
        ("integration_branch", "deleted"),
        ("task_branch", "deleted"),
        ("temporary_backups", "removed"),
    ):
        complete = complete.cleanup_completed(component, outcome)
    assert complete.status == "integrated"
    assert complete.cleanup == {
        "task_worktree": "removed",
        "integration_worktree": "removed",
        "task_branch": "deleted",
        "integration_branch": "deleted",
        "temporary_backups": "removed",
    }
    assert complete.cleanup_completed("task_branch", "deleted") == complete
