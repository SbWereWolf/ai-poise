import pytest

from harness.modules.foundation.errors import DomainError
from harness.modules.result_integration.domain import IntegrationIntent, IntegrationRun


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
    run = IntegrationRun.new(intent()).start({"target_before": "b" * 40})
    run = run.await_resolution(["src/a.py"], {"exit_code": 1})

    with pytest.raises(DomainError, match="every observed conflict"):
        run.continue_with([])

    continued = run.continue_with([
        {"path": "src/a.py", "resolution": "Combined the accepted source and target behavior."}
    ])
    assert continued.status == "running"
    assert continued.resolutions[0]["path"] == "src/a.py"


def test_cleanup_progress_is_monotonic_and_replayable():
    run = IntegrationRun.new(intent()).start({"target_before": "b" * 40})
    run = run.integrated("c" * 40, {"exit_code": 0})
    pending = run.cleanup_blocked("worktree", {"reason": "locked"})
    assert pending.status == "cleanup_pending"
    assert pending.target_after == "c" * 40

    complete = pending.worktree_removed().branch_deleted()
    assert complete.status == "integrated"
    assert complete.cleanup == {"worktree": "removed", "branch": "deleted"}
    assert complete.branch_deleted() == complete
