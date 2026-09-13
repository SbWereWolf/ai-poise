"""Real storage contention and atomic ownership-release regressions for Task 0082."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import shutil
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
    selected = runtime.cfg['accounting']['storage']
    assert optional.path.resolve() == (runtime.state / selected['database']).resolve()
    assert optional.lock.resolve() == (runtime.state / selected['lock']).resolve()
    assert not authoritative_accesses, authoritative_accesses
    # The test would otherwise pass if the wired worker silently discarded work.
    stored = runtime.accounting.port.repo.snapshot()["telemetry"]
    assert any(json.loads(row["data"])["telemetry"] == _raw_telemetry() for row in stored)


def test_failed_handoff_preserves_authoritative_cycle_and_replay_closes_it_atomically(project):
    runtime, work, _ = setup(project)
    runtime.telemetry.flush()
    # Task-cycle accounting remains authoritative and is never copied into the
    # separate optional telemetry store.
    authoritative_cycles = SqliteAccounting(
        runtime.store.database,
        runtime.cfg["project"],
        MetricPolicy.parse(runtime.cfg["accounting"]),
    )
    first = ClockObservation("2026-09-13T00:00:00+00:00", 10, "handoff-boot")
    last = ClockObservation("2026-09-13T00:00:01+00:00", 1_000_000_010, "handoff-boot")
    authoritative_cycles.start(runtime.session, runtime.current_task(), first, "same-native-turn")
    authoritative_cycles.touch(runtime.session, last)

    def row():
        with runtime.store.transaction() as db:
            return dict(db.execute("SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL", (runtime.session,)).fetchone())

    before = row()
    packet = request("handoff", {
        "request_id": "atomic-cycle-release",
        "reason": "verify ownership and authoritative accounting atomicity",
        "result": None,
        "commit_message": "test: preserve authoritative ownership cycle",
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
            raise NonAtomicRelease("failed handoff changed the authoritative accounting row")
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
    authoritative_cycles.start(runtime.session, runtime.current_task(), last, "same-native-turn")
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


def _finish_run(directory, output, errors, *, expected, marker, exit_code):
    if expected:
        # This exact mkdtemp directory belongs to this invocation only.
        shutil.rmtree(directory)
        print(marker)
        return exit_code
    (directory / "pytest.txt").write_text(output)
    (directory / "errors.json").write_text(json.dumps(errors, indent=2))
    print(output)
    print(f"Unexpected verification outcome; preserved diagnostics: {directory}", file=sys.stderr)
    return 2


@pytest.mark.parametrize("exit_code,marker", [(0, "GREEN"), (1, "RED")])
def test_runner_cleans_only_its_expected_workspace(tmp_path, capsys, exit_code, marker):
    owned = Path(mkdtemp(dir=tmp_path))
    (owned / "data.txt").write_text("scratch")
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("preserve")
    assert _finish_run(owned, "output", {}, expected=True, marker=marker, exit_code=exit_code) == exit_code
    assert not owned.exists()
    assert foreign.read_text() == "preserve"
    captured = capsys.readouterr()
    assert captured.out == marker + "\n" and captured.err == ""


def test_runner_surfaces_and_preserves_unexpected_workspace(tmp_path, capsys):
    owned = Path(mkdtemp(dir=tmp_path))
    (owned / "data.txt").write_text("failed-state")
    assert _finish_run(owned, "failure", {"case": "unexpected"}, expected=False, marker="", exit_code=0) == 2
    assert (owned / "data.txt").read_text() == "failed-state"
    assert (owned / "pytest.txt").read_text() == "failure"
    assert json.loads((owned / "errors.json").read_text()) == {"case": "unexpected"}
    assert str(owned) in capsys.readouterr().err


def main(mode, evidence_root):
    if mode not in {"red", "green", "guard"}:
        raise ValueError("explicit verification mode required")
    # Unexpected runs retain an explicitly surfaced directory. Expected RED is
    # a successful verification outcome and leaves no opaque runtime tree.
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

    source = Path(__file__).resolve()
    config_tests = source.with_name("test_storage_config.py")
    selection = ([str(config_tests) + "::test_explicit_storage_policy_accepts_operator_paths"] if mode == "red"
                 else [str(source), "-k", "test_runner_"] if mode == "guard"
                 else [str(source), str(config_tests)])
    with redirect_stdout(captured), redirect_stderr(captured):
        code = pytest.main([*selection, "-q", "--basetemp", str(directory / "pytest")], plugins=[Results()])
    expected = {
        "test_explicit_storage_policy_accepts_operator_paths": (
            "call", "DomainError",
            "accounting config: required explicit fields ['causes', 'max_blob_bytes', 'max_events', 'max_files', 'path_categories', 'sources', 'time_mode', 'timezone', 'tokenizer', 'week_start']",
        ),
    }
    calls = [report for report in reports if report.when == "call"]
    matched = (code == pytest.ExitCode.TESTS_FAILED and errors == expected and len(calls) == 1
               if mode == "red" else code == pytest.ExitCode.OK and not errors
               and len(calls) == (3 if mode == "guard" else 46)
               and all(report.passed for report in calls))
    marker = {"red": "EXPECTED_EXPLICIT_TELEMETRY_STORAGE_CONTRACT_MISSING",
              "green": "TELEMETRY_STORAGE_GREEN", "guard": "TELEMETRY_RUNNER_GREEN"}[mode]
    return _finish_run(directory, captured.getvalue(), errors, expected=matched,
                       marker=marker, exit_code=1 if mode == "red" else 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
