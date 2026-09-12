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
    resources = [
        {"kind": "worktree", "identity": "git-owner", "path": "/worktrees/T1"},
        {"kind": "branch", "name": "tasks/T1", "commit": "a" * 40},
    ]
    run = run.integrated("c" * 40, {"exit_code": 0}, resources)
    pending = run.cleanup_blocked(run.cleanup.resources[0], {"reason": "locked"})
    assert pending.status == "cleanup_pending"
    assert pending.target_after == "c" * 40

    after_worktree = pending.cleanup_resource_removed(pending.cleanup.resources[0])
    complete = after_worktree.cleanup_resource_removed(after_worktree.cleanup.resources[0])
    assert complete.status == "integrated"
    assert complete.cleanup.status == "cleanup_complete"
    assert complete.cleanup.disposition.kind == "integrated"
    assert complete.cleanup.remaining_resources() == ()
    assert complete.cleanup_resource_removed(resources[1]) == complete


def test_integration_cleanup_advances_temporary_resources_without_branch_aliasing():
    run = IntegrationRun.new(intent()).start({"target_before": "b" * 40})
    resources = [
        {"kind": "temporary", "path": "/runtime/T1/index", "digest": "d" * 64},
        {"kind": "temporary_backup", "path": "/runtime/T1/backup", "digest": "e" * 64},
    ]
    run = run.integrated("c" * 40, {"exit_code": 0}, resources)

    first = run.cleanup_resource_removed(run.cleanup.resources[0])
    complete = first.cleanup_resource_removed(first.cleanup.resources[0])

    assert first.status == "cleanup_pending"
    assert complete.status == "integrated"
    assert complete.cleanup.status == "cleanup_complete"
