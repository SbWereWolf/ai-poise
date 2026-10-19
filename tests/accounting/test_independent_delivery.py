"""Independent records must finish while another record remains held."""
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from poise.application.telemetry import OptionalTelemetry, TelemetryEnvelope
from poise.common import PoiseError
from poise.infrastructure.telemetry import AsyncTelemetryDispatcher, DetachedTelemetryProcessor
from poise.infrastructure.telemetry_spool import TelemetrySpool
from poise.modules.accounting.clock import ClockObservation
from tests.accounting.test_paths import setup
from tests.batch.helpers import request


DATA = Path(__file__).parent / "fixtures" / "independent_delivery_data.json"


def _data():
    return json.loads(DATA.read_text(encoding="utf-8"))


class _Clock:
    def __init__(self):
        self.observed = Event()
        self.value = _data()["clock"]

    def observe(self):
        self.observed.set()
        return ClockObservation(**self.value)


class _ForkClock(_Clock):
    fork_safe = True


class _Action:
    """A transparent test caller; exceptions remain available to the test."""

    def __init__(self, action):
        self.done = Event()
        self.result = None
        self.error = None

        def run():
            try:
                self.result = action()
            except BaseException as exc:
                self.error = exc
            finally:
                self.done.set()

        self.thread = Thread(target=run, daemon=True)
        self.thread.start()

    def join(self):
        self.thread.join(5)
        if self.thread.is_alive():
            pytest.fail("test caller did not stop after release")

    def require_success(self):
        self.join()
        if self.error is not None:
            raise self.error
        return self.result


def _entered(event):
    if not event.wait(5):
        pytest.fail("first operation did not enter the intended test gate")


def _capture(service, turn):
    token = service.capture("show", None, None, turn)
    if token is None:
        pytest.fail("capture was not admitted with explicit available capacity")
    return token


def _complete(service, token):
    if not service.complete(token, None, {"status": "read_only"}):
        pytest.fail("capture completion was rejected")


def _spool(directory, sink, now, *, batch_items=1):
    policy = _data()["delivery_policy"]
    policy["batch_items"] = batch_items
    return TelemetrySpool(directory, policy, sink, now=lambda: now[0])


def test_thread_processor_delivers_second_before_first_release():
    entered, release, second = Event(), Event(), Event()
    received = []

    def process(item):
        if item.identity == "first":
            entered.set()
            release.wait()
        received.append(item.identity)
        if item.identity == "second":
            second.set()

    dispatch = AsyncTelemetryDispatcher(process, 4)
    try:
        if not dispatch.submit(TelemetryEnvelope("first", {"raw": 17})):
            pytest.fail("first submit was rejected")
        _entered(entered)
        if not dispatch.submit(TelemetryEnvelope("second", {"raw": 18})):
            pytest.fail("second submit was rejected despite available capacity")
        assert second.wait(5), "independent-thread-record-stalled"
        assert not release.is_set()
        assert received == ["second"]
    finally:
        release.set()
        dispatch.close()
    assert received == ["second", "first"]
    assert dispatch.summary()["processed"] == 2


def test_capture_wait_does_not_block_independent_completed_capture():
    second = Event()
    received = []

    def process(item):
        received.append(item.data["turn_id"])
        if item.data["turn_id"] == "second":
            second.set()

    clock = _Clock()
    dispatch = AsyncTelemetryDispatcher(process, 4)
    service = OptionalTelemetry(clock, dispatch, "independent-capture")
    first = _capture(service, "first")
    try:
        _entered(clock.observed)
        _complete(service, _capture(service, "second"))
        assert second.wait(5), "independent-capture-stalled"
        assert received == ["second"]
    finally:
        _complete(service, first)
        dispatch.close()
    assert received == ["second", "first"]


def test_detached_processor_with_injected_clock_delivers_second_before_first_release():
    entered, release, second = Event(), Event(), Event()
    received = []

    def process(item):
        if item.data["turn_id"] == "first":
            entered.set()
            release.wait()
        received.append(item.data["turn_id"])
        if item.data["turn_id"] == "second":
            second.set()

    dispatch = AsyncTelemetryDispatcher(DetachedTelemetryProcessor(process), 4)
    service = OptionalTelemetry(_Clock(), dispatch, "injected-clock")
    try:
        _complete(service, _capture(service, "first"))
        _entered(entered)
        _complete(service, _capture(service, "second"))
        assert second.wait(5), "injected-clock-delivery-stalled"
        assert not release.is_set()
        assert received == ["second"]
    finally:
        release.set()
        dispatch.close()
    assert received == ["second", "first"]


def test_spool_consumer_delivers_other_record_while_first_inflight(tmp_path):
    entered, release, second_delivered = Event(), Event(), Event()
    now = [100]
    effects = []

    def first_sink(item):
        entered.set()
        release.wait()
        effects.append(item.identity)

    def second_sink(item):
        effects.append(item.identity)
        if item.identity == "second":
            second_delivered.set()

    first = _spool(tmp_path / "spool", first_sink, now)
    second = _spool(tmp_path / "spool", second_sink, now)
    first.enqueue(TelemetryEnvelope("first", {"raw": 17}))
    first_call = _Action(first.replay)
    second_call = None
    try:
        _entered(entered)
        now[0] = 105
        second.enqueue(TelemetryEnvelope("second", {"raw": 18}))
        second_call = _Action(second.replay)
        finished = second_call.done.wait(5)
        if second_call.error is not None and not (
            isinstance(second_call.error, PoiseError) and "lock" in str(second_call.error)
        ):
            raise second_call.error
        assert finished and second_delivered.is_set(), "independent-replay-record-stalled"
        assert not release.is_set()
        assert second_call.error is None
        assert second_call.result == {"attempted": 1, "delivered": 1, "failed": 0, "corrupt": 0}
        assert effects == ["second"]
        status = second.status()
        assert status["pending"] == 1
        assert [r["identity"] for r in status["records"]] == ["first"]
        assert status["records"][0]["attempts"] == 1
        assert status["records"][0]["last_error"] == "attempt_without_ack"
    finally:
        release.set()
        first_call.join()
        if second_call is not None:
            second_call.join()
    assert first_call.require_success()["delivered"] == 1
    assert sorted(effects) == ["first", "second"]
    assert first.status()["pending"] == 0


def test_spool_batch_delivers_second_before_first_release(tmp_path):
    entered, release, other = Event(), Event(), Event()
    lock = Lock()
    calls = []
    effects = []

    def sink(item):
        with lock:
            calls.append(item.identity)
            first = len(calls) == 1
        if first:
            entered.set()
            release.wait()
        effects.append(item.identity)
        if not first:
            other.set()

    spool = _spool(tmp_path / "spool", sink, [100], batch_items=2)
    spool.enqueue(TelemetryEnvelope("first", {"raw": 17}))
    spool.enqueue(TelemetryEnvelope("second", {"raw": 18}))
    action = _Action(spool.replay)
    try:
        _entered(entered)
        assert other.wait(5), "independent-batch-record-stalled"
        assert not release.is_set()
        assert len(effects) == 1
    finally:
        release.set()
        action.join()
    assert action.require_success() == {"attempted": 2, "delivered": 2, "failed": 0, "corrupt": 0}
    assert sorted(effects) == ["first", "second"]
    assert spool.status()["pending"] == 0


def test_linux_detached_spool_delivers_second_before_first_release(tmp_path):
    context = multiprocessing.get_context("fork")
    entered, release, second = context.Event(), context.Event(), context.Event()

    def sink(item):
        turn = item.data["turn_id"]
        if turn == "first":
            entered.set()
            release.wait()
        (tmp_path / turn).write_text(turn, encoding="utf-8")
        if turn == "second":
            second.set()

    spool = _spool(tmp_path / "spool", sink, [100])
    dispatch = AsyncTelemetryDispatcher(DetachedTelemetryProcessor(spool), 4)
    service = OptionalTelemetry(_ForkClock(), dispatch, "linux-detached")
    try:
        _complete(service, _capture(service, "first"))
        _entered(entered)
        _complete(service, _capture(service, "second"))
        assert second.wait(5), "detached-spool-record-stalled"
        assert not release.is_set()
        assert (tmp_path / "second").read_text(encoding="utf-8") == "second"
        assert not (tmp_path / "first").exists()
    finally:
        release.set()
        dispatch.close()
    assert (tmp_path / "first").read_text(encoding="utf-8") == "first"
    assert spool.status()["pending"] == 0
    assert dispatch.summary()["processed"] == 2


def test_worktools_verify_saves_second_record_while_first_sink_delayed(project, monkeypatch):
    runtime, tools, initial = setup(project, clock=_Clock())
    runtime.telemetry.flush()
    dispatch = runtime.telemetry.dispatcher
    original = dispatch.processor.process
    entered, release, committed = Event(), Event(), Event()
    raw = _data()

    def process(item):
        data = item.data.get("telemetry")
        usage = [] if data is None else data["usage"]
        stream = None if not usage else usage[0]["stream"]
        if stream == "independent-first":
            entered.set()
            release.wait()
        result = original(item)
        if stream == "independent-second":
            committed.set()
        return result

    monkeypatch.setattr(dispatch.processor, "process", process)
    try:
        first = request("show", {"queries": [{"id": "task", "kind": "task"}]})
        first["telemetry"] = raw["first_usage"]
        tools.invoke(first)
        _entered(entered)
        result = deepcopy(initial["result_template"])
        result["sections"]["report"] = "Independent optional delivery"
        result["commit_message"] = "Save fixture result"
        packet = request("verify", {"result": result, "artifacts": []})
        packet["telemetry"] = raw["second_usage"]
        verified = tools.invoke(packet)
        assert verified["status"] == "verified"
        with runtime.store.database.read_transaction() as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", ("T1",)).fetchone()
            assert row["status"] == "verified"
        assert committed.wait(5), "independent-accounting-commit-stalled"
        assert not release.is_set()
        with runtime.accounting.port.telemetry_repo.database.read_transaction() as db:
            rows = db.execute(
                "SELECT source,stream,sequence,data FROM accounting_usage "
                "WHERE stream IN (?,?) ORDER BY stream",
                ("independent-first", "independent-second"),
            ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0][:3]) == ("test", "independent-second", 1)
        stored = json.loads(rows[0]["data"])
        assert stored["sample"]["counters"] == raw["expected_counters"]
        assert stored["contribution"]["total_tokens"] == 8
    finally:
        release.set()
        dispatch.close()
    with runtime.accounting.port.telemetry_repo.database.read_transaction() as db:
        rows = db.execute(
            "SELECT stream FROM accounting_usage WHERE stream IN (?,?) ORDER BY stream",
            ("independent-first", "independent-second"),
        ).fetchall()
    assert [row["stream"] for row in rows] == ["independent-first", "independent-second"]
    assert runtime.task_queries.record("T1")["status"] == "verified"
