"""Independent connections contend on the actual serialized Task journal owner."""

from pathlib import Path
from threading import Barrier, Event, Thread, current_thread

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure import locking
from poise.infrastructure.sqlite.uow import SqliteUnitOfWork
from runtime_services.restart_auto_noop_support import (
    assert_noop_response, assert_single_noop, noop_snapshot,
)
from runtime_services.restart_auto_support import prepared


@pytest.mark.parametrize("contender", ["identical", "target", "mode"])
def test_concurrent_noop_identity_has_one_terminal_result(project, monkeypatch, contender):
    case = prepared(project)
    actor = case["client"].runtime.session
    clients = {name: WorkTools(WorkPoise(project["config_path"], actor))
               for name in ("noop-winner", "noop-contender")}
    assert clients["noop-winner"].runtime.store.database is not clients["noop-contender"].runtime.store.database
    lock_path = case["client"].runtime.store.database.lock
    before = noop_snapshot(case)
    start = Barrier(2)
    row_pending, contender_attempted, committed = Event(), Event(), Event()
    observed = []
    responses, errors = {}, {}
    original_exit = SqliteUnitOfWork.__exit__
    original_flock = locking.fcntl.flock

    def exit_at_actual_row(unit, exc_type, exc, traceback):
        if current_thread().name == "noop-winner" and not row_pending.is_set():
            row = unit.tasks.db.execute(
                "SELECT event FROM journal WHERE task_id=? "
                "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
                ("T1", "N"),
            ).fetchone()
            if row is not None:
                # Hold the real uncommitted owner transaction until the second
                # connection is actually refused by the real kernel lock.
                row_pending.set()
                contender_attempted.wait()
                observed.append(("pending", row[0]))
                try:
                    return original_exit(unit, exc_type, exc, traceback)
                finally:
                    committed.set()
        return original_exit(unit, exc_type, exc, traceback)

    def flock_at_contender(handle, operation):
        try:
            return original_flock(handle, operation)
        except BlockingIOError:
            if (current_thread().name == "noop-contender"
                    and Path(handle.name) == lock_path
                    and row_pending.is_set() and not committed.is_set()):
                observed.append(("blocked_by_kernel", True))
                contender_attempted.set()
            raise

    monkeypatch.setattr(SqliteUnitOfWork, "__exit__", exit_at_actual_row)
    monkeypatch.setattr(locking.fcntl, "flock", flock_at_contender)

    def worker(name):
        try:
            start.wait()
            if name == "noop-contender":
                row_pending.wait()
            arguments = {"request_id": "N", "task_id": "T1"}
            if name == "noop-winner" or contender == "identical":
                arguments["target_stage"] = "tests"
            elif contender == "target":
                arguments["target_stage"] = "implementation"
            responses[name] = clients[name].invoke(request("advance", arguments))
        except BaseException as error:
            errors[name] = error
        finally:
            # An absent row/early caller error must fail assertions, not leave
            # the other test worker permanently waiting for a nonexistent hook.
            if name == "noop-winner":
                row_pending.set()
            else:
                contender_attempted.set()

    threads = [Thread(target=worker, args=(name,), name=name) for name in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
        assert not thread.is_alive()
    assert "noop-winner" not in errors, errors
    assert ("blocked_by_kernel", True) in observed
    assert committed.is_set()
    assert_noop_response(responses["noop-winner"], case["saved"])
    assert ("pending", "progression.noop") in observed
    if contender == "identical":
        assert "noop-contender" not in errors, errors
        assert_noop_response(responses["noop-contender"], case["saved"])
    else:
        assert "noop-contender" not in responses
        assert isinstance(errors.get("noop-contender"), PoiseError)
        assert "conflict" in str(errors["noop-contender"]).lower()
    assert_single_noop(before, noop_snapshot(case), commit=case["saved"])

