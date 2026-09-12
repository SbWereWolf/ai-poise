import importlib
import importlib.util


def cleanup_domain():
    package = importlib.util.find_spec("poise.modules.task_cleanup")
    assert package is not None, "TaskResourceCleanup domain is not implemented"
    spec = importlib.util.find_spec("poise.modules.task_cleanup.domain")
    assert spec is not None, "TaskResourceCleanup domain is not implemented"
    return importlib.import_module("poise.modules.task_cleanup.domain")


def intent(module, disposition=None):
    return module.CleanupIntent.parse({
        "request_id": "cleanup-1",
        "task_id": "T1",
        "authorization": "User selected the exact commit disposition.",
        "commit_disposition": disposition,
    })


def resources():
    return [
        {"kind": "worktree", "identity": "gitdir-1", "path": "/state/worktrees/T1"},
        {"kind": "branch", "name": "tasks/T1", "commit": "a" * 40},
        {"kind": "temporary", "path": "/runtime/T1/temp", "digest": "b" * 64},
    ]


def test_terminal_status_is_not_a_commit_disposition():
    module = cleanup_domain()
    run = module.CleanupRun.new(intent(module), resources())

    assert run.status == "disposition_required"
    assert run.remaining_resources() == tuple(resources())
    assert run.complete is False


def test_cleanup_progress_is_monotonic_blockable_and_replayable():
    module = cleanup_domain()
    disposition = {"kind": "discard_authorized", "expected_commit": "a" * 40}
    run = module.CleanupRun.new(intent(module, disposition), resources())

    blocked = run.cleanup_blocked(resources()[0], {"actual_exit_code": 1})
    assert blocked.status == "cleanup_blocked"
    assert blocked.remaining_resources() == tuple(resources())
    resumed = blocked.retry_blocked()
    without_temp = resumed.resource_removed(resources()[2])
    without_worktree = without_temp.resource_removed(resources()[0])
    complete = without_worktree.resource_removed(resources()[1])

    assert complete.status == "cleanup_complete"
    assert complete.remaining_resources() == ()
    assert complete.resource_removed(resources()[1]) == complete


def test_branch_removal_requires_worktree_and_resolved_disposition():
    module = cleanup_domain()
    run = module.CleanupRun.new(intent(module), resources())

    for forbidden in (resources()[1], resources()[0]):
        try:
            run.resource_removed(forbidden)
        except module.DomainError:
            pass
        else:
            raise AssertionError("cleanup ordering or disposition was bypassed")
