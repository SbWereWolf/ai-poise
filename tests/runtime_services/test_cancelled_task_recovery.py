"""Public cancellation recovery preserves work; it does not restart the contract."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import bootstrap, configure, request, result, verify
from conftest import WorkPoise as Poise, add_test
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from poise.modules.tasks.domain import TaskStatus
from runtime_services.test_task_restart import execution, git, immutable_audit_rows, work_packet_rows, wip_state


def recover(tools, task_id, version, **updates):
    args = dict(action="recover_cancelled", request_id="undo-cancel", task_id=task_id,
                expected_version=version, reason="This unfinished Task was cancelled accidentally.",
                authorization="User explicitly authorizes restoring this Task.")
    args.update(updates)
    return tools.invoke(request("task", args))


def cancelled(project, verified=False):
    configure(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = bootstrap(tools, project)
    root = Path(context["worktree"])
    if verified:
        add_test(root)
        assert verify(tools, result(context))["status"] == "verified"
    prior = deepcopy(tools.runtime.task_queries.record(context["task"]))
    (root / "src" / "double.py").write_text("def double(value):\n    return value * 3\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "restart-staged.txt").write_text("staged WIP\n")
    git(root, "add", "tests/restart-staged.txt")
    (root / "restart-untracked.txt").write_text("untracked WIP\n")
    tools.invoke(request("cancel", {"reason": "User cancellation, later found accidental."}))
    return tools, context["task"], root, prior


@pytest.mark.parametrize("verified", [False, True])
def test_recover_same_task_preserves_exact_work_and_audit(project, verified):
    tools, task_id, root, prior = cancelled(project, verified)
    h = tools.runtime
    before = h.task_queries.record(task_id)
    audit, packets, work = immutable_audit_rows(h, task_id), work_packet_rows(h, task_id), wip_state(root)
    old_execution = execution(h, task_id)
    assert bootstrap(tools, {"task": {"id": task_id}})["status"] == "cancelled"
    restored = recover(tools, task_id, before["version"])
    assert restored["status"] == "task_recovered"
    assert restored["task_status"] == prior["status"]
    assert restored["replayed"] is False
    after = h.task_queries.record(task_id)
    for key in ("id", "sprint_id", "stage_index", "iteration", "branch", "worktree", "contract", "last_report"):
        assert after[key] == before[key]
    assert after["version"] == before["version"] + 1
    assert after["claimed_by"] is None
    assert execution(h, task_id) == {**old_execution, "pending": None}
    assert wip_state(root) == work
    assert work_packet_rows(h, task_id) == packets
    newer = immutable_audit_rows(h, task_id)
    for table, rows in audit.items():
        assert newer[table][:len(rows)] == rows
    assert after["history"][-1]["event"] == "cancellation_recovered"
    assert recover(tools, task_id, before["version"])["replayed"] is True
    assert h.task_queries.record(task_id) == after
    assert wip_state(root) == work
    with pytest.raises(PoiseError, match="Request ID.*another"):
        recover(tools, task_id, before["version"], reason="different intent")
    # The ordinary ownership path can continue; no cancelled terminal is bypassed.
    h.ownership.acquire_task(task_id)
    assert h.current_task()["status"] == prior["status"]


@pytest.mark.parametrize("fault,pattern", [
    ("stale", "version"), ("authorization", "authorization"),
    ("pending", "pending|outcome"), ("foreign", "owned|ownership"),
    ("ambiguous", "multiple owners"), ("wrong_branch", "worktree|branch"),
    ("completed", "cancelled|terminal"), ("integrated", "integrat|publication"),
])
def test_recovery_rejection_never_changes_task_or_work(project, fault, pattern):
    tools, task_id, root, prior = cancelled(project)
    h = tools.runtime
    if fault in ("pending", "integrated"):
        with h.store.unit_of_work() as uow:
            patch = {"pending": "checks"} if fault == "pending" else {"publication": {"status": "integrated"}}
            uow.execution.patch(task_id, patch)
    elif fault in ("foreign", "ambiguous"):
        with h.store.unit_of_work() as uow:
            if fault == "foreign":
                uow.ownership.bind_worktree("executor", None)
            else:
                # Model a historical ambiguous binding, not a valid new write.
                uow.tasks.db.execute("DROP INDEX sessions_single_worktree_owner")
            uow.ownership.bind_worktree("foreign", task_id)
    elif fault == "wrong_branch":
        git(root, "checkout", "-b", "foreign-branch")
    elif fault == "completed":
        with h.store.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            uow.tasks.save(task._change("fixture_completed", None, None, status=TaskStatus.COMPLETED), task.state.version)
    before = h.task_queries.record(task_id)
    audit, state, work = immutable_audit_rows(h, task_id), execution(h, task_id), wip_state(root)
    args = {"expected_version": before["version"] - 1} if fault == "stale" else {}
    if fault == "authorization":
        args["authorization"] = ""
    expected = args.pop("expected_version", before["version"])
    with pytest.raises(PoiseError, match=pattern):
        recover(tools, task_id, expected, **args)
    assert h.task_queries.record(task_id) == before
    assert immutable_audit_rows(h, task_id) == audit
    assert execution(h, task_id) == state
    assert wip_state(root) == work


def test_recovery_and_receipt_commit_or_rollback_together(project, monkeypatch):
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
    tools, task_id, root, _ = cancelled(project, True)
    h = tools.runtime
    before, state = h.task_queries.record(task_id), execution(h, task_id)
    audit = immutable_audit_rows(h, task_id)
    def fail(*args, **kwargs):
        raise PoiseError("injected receipt persistence failure")
    with monkeypatch.context() as patch:
        patch.setattr(SqliteTaskRepository, "remember_action", fail)
        with pytest.raises(PoiseError, match="injected receipt"):
            recover(tools, task_id, before["version"])
    assert h.task_queries.record(task_id) == before
    assert execution(h, task_id) == state
    assert immutable_audit_rows(h, task_id) == audit
    assert recover(tools, task_id, before["version"])["task_status"] == "verified"


def test_recovery_preserves_started_disposition_by_rejecting_it(project):
    from poise.modules.task_cleanup.domain import CleanupRun, CommitDisposition
    tools, task_id, root, _ = cancelled(project)
    h = tools.runtime
    before = h.task_queries.record(task_id)
    with h.store.unit_of_work() as uow:
        data, _ = uow.execution.load(task_id)
        run = CleanupRun.restore(data["pending"])
        run = run.with_disposition(CommitDisposition("discard_authorized", expected_commit=git(root, "rev-parse", "HEAD")))
        uow.execution.patch(task_id, {"pending": {"kind": "task_cleanup", **run.to_storage()}})
    data = execution(h, task_id)
    with pytest.raises(PoiseError, match="pending cleanup/disposition"):
        recover(tools, task_id, before["version"])
    assert execution(h, task_id) == data
    assert h.task_queries.record(task_id)["status"] == "cancelled"


def test_a_cancelled_completed_task_cannot_be_made_unfinished(project):
    tools, task_id, root, _ = cancelled(project)
    h = tools.runtime
    with h.store.unit_of_work() as uow:
        task = uow.tasks.load(task_id)
        done = task._change("fixture_completed", None, None, status=TaskStatus.COMPLETED)
        uow.tasks.save(done, task.state.version)
        uow.tasks.save(done.task.cancel("executor", "cancel after completion"), done.task.state.version)
    before = h.task_queries.record(task_id)
    with pytest.raises(PoiseError, match="terminal completed/integrated"):
        recover(tools, task_id, before["version"])
    assert h.task_queries.record(task_id) == before


def test_recovery_preserves_sprint_membership(project):
    from sprints.helpers import setup, draft, publish, task, bootstrap as boot
    configure(project); setup(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    saved = draft(tools, [task(project)])
    publish(tools, saved["revision"])
    context = boot(tools, "A")
    tools.invoke(request("cancel", {"reason": "accidental cancellation"}))
    before = tools.runtime.sprint_tools.overview("S")
    record = tools.runtime.task_queries.record("A")
    restored = recover(tools, "A", record["version"])
    assert restored["task_status"] == "active"
    after = tools.runtime.task_queries.record("A")
    assert after["sprint_id"] == "S"
    assert after["worktree"] == context["worktree"]
    overview = tools.runtime.sprint_tools.overview("S")
    assert [item["id"] for item in overview["tasks"]] == [item["id"] for item in before["tasks"]]


def test_old_cancellation_without_exact_prior_state_is_not_guessed(project):
    import json
    tools, task_id, root, _ = cancelled(project)
    h = tools.runtime
    before = h.task_queries.record(task_id)
    with h.store.unit_of_work() as uow:
        row = uow.tasks.db.execute("SELECT seq,data FROM task_events WHERE task_id=? AND version=?", (task_id, before["version"])).fetchone()
        value = json.loads(row["data"]); value.pop("previous")
        uow.tasks.db.execute("UPDATE task_events SET data=? WHERE seq=?", (json.dumps(value), row["seq"]))
    before = h.task_queries.record(task_id)
    with pytest.raises(PoiseError, match="no exact recovery point"):
        recover(tools, task_id, before["version"])
    assert h.task_queries.record(task_id) == before
