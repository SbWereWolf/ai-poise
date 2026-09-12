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
        "refs/heads/tasks/T1",
        "/state/worktrees/T1",
        "/state/runtime/session/result-integration/T1/example",
    ).begin_update("b" * 40)
    run = run.await_resolution(["src/a.py"], {"exit_code": 1})

    with pytest.raises(DomainError, match="every observed conflict"):
        run.continue_with([])

    continued = run.continue_with([
        {"path": "src/a.py", "resolution": "Combined the accepted source and target behavior."}
    ])
    assert continued.status == "running"
    assert continued.resolutions[0]["path"] == "src/a.py"


def test_failed_checks_can_return_to_the_same_candidate_for_retry():
    run = IntegrationRun.new(
        intent(), "tasks/T1", "/worktrees/T1", "/runtime/integration/T1"
    )
    run = run.begin_update("b" * 40)
    run = run.candidate_ready("c" * 40, {"actual_exit_code": 0})
    failed = run.checks_recorded([{"method": "M", "passed": False}])

    retried = failed.retry_checks()

    assert retried.status == "running"
    assert retried.phase == "candidate_ready"
    assert retried.integration_head == "c" * 40
    assert retried.failure is None
    assert retried.history[-1]["event"] == "checks_retry_started"


def test_cleanup_progress_is_monotonic_and_replayable():
    run = IntegrationRun.new(
        intent(),
        "refs/heads/tasks/T1",
        "/state/worktrees/T1",
        "/state/runtime/session/result-integration/T1/example",
    ).begin_update("b" * 40)
    run = run.candidate_ready("c" * 40, {"exit_code": 0})
    run = run.checks_recorded([])
    run = run.publication_confirmed(
        "refs/heads/main", {"exit_code": 0}, no_op=False, recovered=False
    )
    pending = run.cleanup_blocked("task_worktree", {"reason": "locked"})
    assert pending.status == "cleanup_pending"
    assert pending.target_after == "c" * 40

    complete = pending
    for component, outcome in (
        ("task_worktree", "removed"),
        ("task_branch", "deleted"),
        ("temporary_backups", "removed"),
    ):
        complete = complete.cleanup_completed(component, outcome)
    assert complete.status == "integrated"
    assert complete.cleanup == {
        "task_worktree": "removed",
        "task_branch": "deleted",
        "temporary_backups": "removed",
    }
    assert complete.cleanup_completed("task_branch", "deleted") == complete


def test_legacy_recovery_is_limited_to_the_named_saved_request():
    legacy = {
        "kind": "result_integration",
        "intent": intent().identity(),
        "status": "blocked",
        "version": 2,
        "target_before": "b" * 40,
        "target_after": None,
        "conflicts": [],
        "resolutions": [],
        "merge": None,
        "failure": {"reason": "merge_failed_without_conflicts", "receipt": {}},
        "cleanup": {"worktree": "pending", "branch": "pending"},
        "history": [],
    }

    with pytest.raises(DomainError, match="not eligible"):
        IntegrationRun.recover_legacy(
            legacy, "refs/heads/tasks/T1", "/state/worktrees/T1",
            "/state/runtime/result-integration/T1/example",
        )
