"""Real storage contention and atomic ownership-release regressions for Task 0082."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import mkdtemp
from threading import Event, Thread, local

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[1])]

from conftest import DeterministicClock
from poise.application.telemetry import OptionalTelemetry
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.sqlite.accounting import SqliteAccounting
from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
from poise.modules.accounting.clock import ClockObservation
from poise.modules.accounting.domain import MetricPolicy
from poise.runtime import Poise
from tests.batch.helpers import request
from tests.accounting.test_optional_telemetry_isolation import _raw_telemetry
from tests.accounting.test_paths import metrics, setup


class StorageCoupling(AssertionError):
    """Only a demonstrated storage-boundary regression, never a setup failure."""


class NonAtomicRelease(AssertionError):
    """Only the expected accounting-row mutation after failed Task release."""


def _show():
    return request("show", {"queries": [{"id": "task", "kind": "task"}]})


def _start(action):
    outcome, done = {}, Event()

    def run():
        try:
            outcome["result"] = action()
        except BaseException as error:
            outcome["error"] = error
        finally:
            done.set()

    thread = Thread(target=run)
    thread.start()
    return thread, outcome, done


def test_real_optional_write_does_not_block_current_next_or_accounting_work(project, monkeypatch):
    runtime, work, _ = setup(project)
    dispatcher = runtime.telemetry.dispatcher
    runtime.telemetry.flush()
    optional = runtime.accounting.port.repo.database
    authoritative = runtime.store.database
    entered, release = Event(), Event()
    context = local()
    authoritative_accesses = []
    process = dispatcher.processor.process

    def observed_process(envelope):
        context.optional = True
        try:
            return process(envelope)
        finally:
            context.optional = False

    # Keep the actual Poise-created dispatcher, callback and SQL operations.
    monkeypatch.setattr(dispatcher.processor, "process", observed_process)
    optional_transaction = optional.transaction

    @contextmanager
    def held_transaction():
        with optional_transaction() as db:
            before = db.total_changes
            yield db
            if getattr(context, "optional", False) and db.total_changes > before and not entered.is_set():
                # SQL has already changed a row; both external and SQLite writer
                # locks remain held until this context commits after release.
                entered.set()
                assert release.wait(15), "optional writer gate was not released"

    monkeypatch.setattr(optional, "transaction", held_transaction)
    main_transaction = authoritative.transaction

    @contextmanager
    def observed_main_transaction():
        if getattr(context, "optional", False):
            authoritative_accesses.append("optional processor acquired authoritative transaction")
        with main_transaction() as db:
            yield db

    monkeypatch.setattr(authoritative, "transaction", observed_main_transaction)
    summary = runtime.interactions.summary

    def ordered_summary(task):
        assert entered.wait(5), "production optional callback never wrote a row"
        return summary(task)  # Never replace the real authoritative query.

    monkeypatch.setattr(runtime.interactions, "summary", ordered_summary)
    packet = _show()
    packet["telemetry"] = _raw_telemetry()
    fresh = []

    def foreground():
        first = work.invoke(packet)
        following = _show()
        following["messages"] = [{
            "conversation_id": "storage-isolation",
            "message_id": "while-optional-write-held",
            "occurred_at": "2026-09-13T00:00:00+00:00",
            "reason": "continue",
            "subject": None,
        }]
        second = work.invoke(following)  # A real mandatory ledger write.
        other = Poise(project["config_path"], "other-session", clock=DeterministicClock())
        fresh.append(other)
        third = WorkTools(other).invoke(_show())  # Next CLI-like composition too.
        report = metrics(work)  # A read must not wait on optional persistence.
        return first, second, third, report

    thread, outcome, done = _start(foreground)
    try:
        assert entered.wait(5), "production processor did not enter its write transaction"
        done.wait(5)  # Deadlock guard only; success is completion with gate CLOSED.
        returned_while_held = done.is_set()
        assert not release.is_set()
    finally:
        release.set()
        thread.join(15)
        assert not thread.is_alive(), "foreground did not terminate after releasing the gate"
        dispatcher.close()
        for other in fresh:
            other.telemetry.dispatcher.close()

    error = outcome.get("error")
    if error is not None:
        if isinstance(error, PoiseError) and "Истёк лимит ожидания внешнего lock" in str(error):
            raise StorageCoupling("successful work was replaced by optional lock timeout")
        raise error
    if not returned_while_held:
        raise StorageCoupling("work waited for the optional write transaction")
    first, second, third, report = outcome["result"]
    assert first["status"] == second["status"] == third["status"] == "read_only"
    assert second["interaction"]["observed_messages_count"] == first["interaction"]["observed_messages_count"] + 1
    assert report["telemetry"]["coverage"] == "partial"
    assert report["telemetry"]["pending"] >= 1
    assert optional.path.resolve() != authoritative.path.resolve()
    assert optional.lock.resolve() != authoritative.lock.resolve()
    assert not authoritative_accesses, authoritative_accesses
    # The test would otherwise pass if the wired worker silently discarded work.
    stored = runtime.accounting.port.repo.snapshot()["telemetry"]
    assert any(json.loads(row["data"])["telemetry"] == _raw_telemetry() for row in stored)


def test_failed_handoff_preserves_legacy_cycle_and_replay_closes_it_atomically(project):
    runtime, work, _ = setup(project)
    runtime.telemetry.flush()
    # This is deliberately the authoritative legacy store, even after optional
    # telemetry moves elsewhere. No migration/copy of historical cycles.
    legacy = SqliteAccounting(runtime.store.database, runtime.cfg["project"], MetricPolicy.parse(runtime.cfg["accounting"]))
    first = ClockObservation("2026-09-13T00:00:00+00:00", 10, "handoff-boot")
    last = ClockObservation("2026-09-13T00:00:01+00:00", 1_000_000_010, "handoff-boot")
    legacy.start(runtime.session, runtime.current_task(), first, "same-native-turn")
    legacy.touch(runtime.session, last)

    def row():
        with runtime.store.transaction() as db:
            return dict(db.execute("SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL", (runtime.session,)).fetchone())

    before = row()
    packet = request("handoff", {
        "request_id": "atomic-cycle-release",
        "reason": "verify ownership and legacy accounting atomicity",
        "result": None,
        "commit_message": "test: preserve legacy ownership cycle",
        "artifact_paths": [],
    })
    with runtime.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_handoff BEFORE UPDATE ON handoffs BEGIN SELECT RAISE(ABORT,'test-fault'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="test-fault"):
            work.invoke(packet)
        assert runtime.current_task()["claimed_by"] == runtime.session
        with runtime.store.transaction() as db:
            after = dict(db.execute("SELECT * FROM accounting_cycles WHERE id=?", (before["id"],)).fetchone())
        if after != before:
            raise NonAtomicRelease("failed handoff changed the legacy accounting row")
    finally:
        with runtime.store.transaction() as db:
            db.execute("DROP TRIGGER fail_handoff")
        runtime.telemetry.flush()

    result = work.invoke(packet)
    assert result["status"] == "handed_off"
    assert runtime.current_task() is None
    with runtime.store.transaction() as db:
        after = dict(db.execute("SELECT * FROM accounting_cycles WHERE id=?", (before["id"],)).fetchone())
    data = json.loads(after["data"])
    assert after["ended_at"] == last.audit_utc
    assert data["timing"]["elapsed_microseconds"] == 1_000_000
    assert data["closed_by"] == "handoff_at_last_observation"
    other = deepcopy(project["task"])
    other.update(id="T2", methods=[], method_inputs=[], checks={"write": []},
                 evidence_plan={"write": {"subject_methods": {}, "arguments": [], "review_arguments": []}})
    result = work.invoke(request("bootstrap", {"task": other, "decision": None, "feedback": None, "rework_stage": None}))
    assert result["status"] == "active"
    legacy.start(runtime.session, runtime.current_task(), last, "same-native-turn")
    replay = work.invoke(packet)
    assert replay["replayed"] is True
    assert runtime.current_task()["id"] == "T2"
    assert row()["task_id"] == "T2"
    runtime.telemetry.dispatcher.close()


def test_accounting_read_returns_with_closed_optional_processor_gate(project):
    runtime, work, _ = setup(project)
    runtime.telemetry.dispatcher.close()
    entered, release = Event(), Event()

    def slow(envelope):
        entered.set()
        assert release.wait(15)

    dispatcher = AsyncTelemetryDispatcher(slow, 4)
    runtime.telemetry.dispatcher = dispatcher
    work.invoke(_show())
    assert entered.wait(5)
    thread, outcome, done = _start(lambda: metrics(work))
    try:
        done.wait(5)
        returned_while_held = done.is_set()
        assert not release.is_set()
    finally:
        release.set()
        thread.join(15)
        dispatcher.close()
    assert not thread.is_alive()
    assert returned_while_held
    assert "error" not in outcome, outcome
    assert outcome["result"]["telemetry"]["coverage"] == "partial"
    assert outcome["result"]["telemetry"]["pending"] >= 1


@pytest.mark.parametrize("mode", ["closed", "full"])
def test_one_rejected_capture_counts_as_one_loss(mode):
    entered, release = Event(), Event()

    class Clock:
        def observe(self):
            entered.set()
            assert release.wait(15)
            return ClockObservation("2026-09-13T00:00:00+00:00", 1, "loss-boot")

    dispatcher = AsyncTelemetryDispatcher(lambda envelope: None, 1)
    service = OptionalTelemetry(Clock(), dispatcher, "loss-session")
    token = None
    try:
        if mode == "closed":
            dispatcher.close()
        else:
            token = service.capture("show", None, None, None)
            assert entered.wait(5)
        assert service.capture("show", None, None, None) is None
        summary = service.summary()
        assert summary["dropped"] == 1
        assert summary["coverage"] == "partial"
    finally:
        if token is not None:
            service.complete(token, None, {"status": "read_only"})
        release.set()
        dispatcher.close()


def main(mode, evidence_root):
    # Keep every failing workspace and log, including expected RED. Never let
    # pytest reuse/delete an earlier diagnostic directory.
    directory = Path(mkdtemp(prefix="0082-storage-", dir=evidence_root))
    captured = io.StringIO()
    reports, errors = [], {}

    class Results:
        def pytest_runtest_logreport(self, report):
            reports.append(report)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            yield
            if call.excinfo is not None:
                errors[item.name] = (call.when, call.excinfo.typename, str(call.excinfo.value))

    with redirect_stdout(captured), redirect_stderr(captured):
        code = pytest.main([str(Path(__file__).resolve()), "-q", "--basetemp", str(directory / "pytest")], plugins=[Results()])
    (directory / "pytest.txt").write_text(captured.getvalue())
    (directory / "errors.json").write_text(json.dumps(errors, indent=2))
    expected = {
        "test_real_optional_write_does_not_block_current_next_or_accounting_work": ("call", "StorageCoupling", "successful work was replaced by optional lock timeout"),
        "test_failed_handoff_preserves_legacy_cycle_and_replay_closes_it_atomically": ("call", "NonAtomicRelease", "failed handoff changed the legacy accounting row"),
    }
    calls = [report for report in reports if report.when == "call"]
    if mode == "red" and code == pytest.ExitCode.TESTS_FAILED and errors == expected and len(calls) == 5 and sum(report.passed for report in calls) == 3:
        print("EXPECTED_STORAGE_CONTENTION_AND_NONATOMIC_HANDOFF")
        return 1
    if mode == "green" and code == pytest.ExitCode.OK and len(calls) == 5 and all(report.passed for report in calls):
        print("TELEMETRY_STORAGE_GREEN")
        return 0
    print(captured.getvalue())
    print(f"Unexpected {mode} outcome; preserved diagnostics: {directory}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
