"""Focused contract for best-effort telemetry outside authoritative work."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from threading import Event, Thread


ROOT = Path(__file__).resolve().parents[2]


class _Interactions:
    def prepare(self, raw):
        return ()

    def record(self, prepared, session, task):
        return None

    def delivered(self, task, report):
        return None

    def summary(self, task):
        return {"coverage": "unavailable"}


class _LegacyAccountingMustNotDriveWork:
    def __getattr__(self, name):
        raise AssertionError(f"authoritative work called legacy accounting.{name}")


class _Runtime:
    def __init__(self, telemetry):
        self.cfg = {"batch": {"max_items": 16}}
        self.session = "original-session"
        self.interactions = _Interactions()
        self.accounting = _LegacyAccountingMustNotDriveWork()
        self.telemetry = telemetry
        self.work_resources = object()
        self.task_queries = object()

    def current_task(self):
        return None

    def show(self):
        return {"state": "authoritative"}

    def report_task(self, report):
        return None


class _Clock:
    def __init__(self):
        from poise.modules.accounting.clock import ClockObservation

        self.values = iter(
            [
                ClockObservation("2026-09-13T01:00:00+00:00", 10, "boot-0082"),
                ClockObservation("2026-09-13T00:59:00+00:00", 30, "boot-0082"),
                ClockObservation("2026-09-13T01:01:00+00:00", 40, "boot-0082"),
                ClockObservation("2026-09-13T01:01:01+00:00", 50, "boot-0082"),
            ]
        )

    def observe(self):
        return next(self.values)


def _raw_telemetry():
    return {
        "usage": [
            {
                "source": "test",
                "stream": "agent",
                "event_id": "usage-1",
                "sequence": 1,
                "mode": "delta",
                "occurred_at": "2026-09-13T00:58:00+00:00",
                "counters": {
                    "input_tokens": 5,
                    "output_tokens": 3,
                    "total_tokens": 8,
                    "cached_input_tokens": 2,
                    "reasoning_tokens": 1,
                },
            }
        ],
        "intervals": [],
        "cause": "initial",
        "finding_targets": [],
    }


def _request():
    return {
        "operation": "show",
        "input": {"queries": [{"id": "task", "kind": "task"}]},
        "messages": [],
        "telemetry": _raw_telemetry(),
    }


def _invoke_with_release(tools, release, entered):
    outcome = {}
    done = Event()

    def invoke():
        try:
            outcome["result"] = tools.invoke(_request())
        except BaseException as error:  # Preserve the exact foreground failure for assertions.
            outcome["error"] = error
        finally:
            done.set()

    thread = Thread(target=invoke)
    thread.start()
    assert entered.wait(1), "optional processor did not receive the envelope"
    returned_before_release = done.wait(0.1)
    release.set()
    thread.join(1)
    assert not thread.is_alive()
    return returned_before_release, outcome


def _old_path_is_synchronous():
    from poise.application.work import WorkTools

    entered = Event()
    release = Event()

    class BlockingAccounting:
        def prepare(self, raw):
            return raw

        def begin(self, *args):
            entered.set()
            release.wait()

        def receive(self, *args):
            return None

        def finish(self, *args):
            return None

    runtime = _Runtime(None)
    runtime.accounting = BlockingAccounting()
    returned, outcome = _invoke_with_release(WorkTools(runtime), release, entered)
    assert "error" not in outcome
    return not returned


def test_slow_optional_processor_does_not_delay_or_replace_work_result():
    from poise.application.telemetry import OptionalTelemetry
    from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
    from poise.application.work import WorkTools

    entered = Event()
    release = Event()
    received = []

    def process(envelope):
        entered.set()
        release.wait()
        received.append(envelope)

    dispatcher = AsyncTelemetryDispatcher(process, max_pending=4)
    telemetry = OptionalTelemetry(_Clock(), dispatcher, "original-session")
    returned, outcome = _invoke_with_release(WorkTools(_Runtime(telemetry)), release, entered)
    dispatcher.close()

    assert returned is True
    assert "error" not in outcome
    assert outcome["result"]["results"][0]["value"] == {"state": "authoritative"}
    assert len(received) == 1


def test_failure_duplicate_and_original_fields_are_isolated_and_observable():
    from poise.application.telemetry import OptionalTelemetry
    from poise.infrastructure.telemetry import AsyncTelemetryDispatcher

    captured = []

    class Capture:
        def submit(self, envelope):
            captured.append(envelope)
            return True

    raw = _raw_telemetry()
    service = OptionalTelemetry(_Clock(), Capture(), "original-session")
    token = service.begin("show", None, raw, "turn-1")
    service.finish(token, None, {"status": "read_only"})
    assert len(captured) == 1
    envelope = captured[0]
    data = envelope.data
    assert data["operation"] == "show"
    assert data["session"] == "original-session"
    assert data["turn_id"] == "turn-1"
    assert data["before_binding"] == data["after_binding"] == {
        "task": None,
        "sprint": None,
        "goal_type": None,
        "stage": None,
        "iteration": None,
    }
    assert data["telemetry"] == raw
    assert data["started"] == {
        "audit_utc": "2026-09-13T01:00:00+00:00",
        "monotonic_ns": 10,
        "comparison_domain": "boot-0082",
    }
    assert data["finished"]["audit_utc"] == "2026-09-13T00:59:00+00:00"
    assert data["finished"]["monotonic_ns"] == 30
    assert "elapsed" not in data and "duration" not in data

    attempted = []
    failed = Event()

    def fail_once(item):
        attempted.append(item.identity)
        failed.set()
        raise RuntimeError("optional sink failed")

    dispatcher = AsyncTelemetryDispatcher(fail_once, max_pending=4)
    assert dispatcher.submit(envelope) is True
    assert dispatcher.submit(envelope) is True
    assert failed.wait(1)
    dispatcher.close()
    assert attempted == [envelope.identity]
    assert dispatcher.summary() == {
        "coverage": "partial",
        "submitted": 2,
        "processed": 0,
        "duplicates": 1,
        "failed": 1,
        "dropped": 0,
    }


def test_optional_capture_failure_is_a_drop_not_a_work_failure():
    from poise.application.telemetry import OptionalTelemetry

    class BrokenClock:
        def observe(self):
            raise RuntimeError("clock unavailable to optional telemetry")

    class RejectingDispatcher:
        def submit(self, envelope):
            raise RuntimeError("queue unavailable")

    service = OptionalTelemetry(BrokenClock(), RejectingDispatcher(), "original-session")
    assert service.begin("show", None, deepcopy(_raw_telemetry()), None) is None
    assert service.finish(None, None, {"status": "read_only"}) is False
    assert service.summary()["coverage"] == "partial"
    assert service.summary()["dropped"] == 1


def test_canonical_documentation_states_optional_boundary_and_limits():
    accounting = (ROOT / "docs/operations/accounting.md").read_text()
    boundaries = (ROOT / "docs/architecture/boundaries.md").read_text()
    assert "необязательная телеметрия" in accounting
    assert "частичн" in accounting
    assert "ClockObservation" in accounting
    assert "TelemetryEnvelope" in boundaries


def _run_red():
    if _old_path_is_synchronous():
        print("EXPECTED_SYNCHRONOUS_TELEMETRY_COUPLING")
        return 1
    raise AssertionError("optional telemetry was already isolated")


def _run_green():
    test_slow_optional_processor_does_not_delay_or_replace_work_result()
    test_failure_duplicate_and_original_fields_are_isolated_and_observable()
    test_optional_capture_failure_is_a_drop_not_a_work_failure()
    print("OPTIONAL_TELEMETRY_GREEN")
    return 0


def _run_documentation():
    test_canonical_documentation_states_optional_boundary_and_limits()
    print("OPTIONAL_TELEMETRY_DOCUMENTATION_GREEN")
    return 0


if __name__ == "__main__":
    modes = {"red": _run_red, "green": _run_green, "documentation": _run_documentation}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        raise SystemExit("usage: test_optional_telemetry_isolation.py red|green|documentation")
    raise SystemExit(modes[sys.argv[1]]())
