"""Focused contract for best-effort telemetry outside authoritative work."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Event, Thread


ROOT = Path(__file__).resolve().parents[2]


class _Interactions:
    def __init__(self, foreground_progress=None):
        self.foreground_progress = foreground_progress

    def prepare(self, raw):
        return ()

    def record(self, prepared, session, task):
        return None

    def delivered(self, task, report):
        return None

    def summary(self, task):
        if self.foreground_progress is not None:
            self.foreground_progress.set()
        return {"coverage": "unavailable"}


class _LegacyAccountingMustNotDriveWork:
    def __getattr__(self, name):
        raise AssertionError(f"authoritative work called legacy accounting.{name}")


class _Runtime:
    def __init__(self, telemetry, foreground_progress=None):
        self.cfg = {"batch": {"max_items": 16}}
        self.session = "original-session"
        self.interactions = _Interactions(foreground_progress)
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
                ClockObservation("2026-09-13T00:59:00+00:00", 2_000_010, "boot-0082"),
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


def _invoke_with_release(tools, release, entered, foreground_progress, *, wait_for_progress):
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
    assert entered.wait(5), "downstream did not reach its deterministic gate"
    if wait_for_progress:
        # The five-second wait is only a deadlock guard.  The assertion is causal:
        # foreground crossed summary while downstream remained behind `release`.
        foreground_progress.wait(5)
    progressed_before_release = foreground_progress.is_set()
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert done.is_set()
    return progressed_before_release, outcome


def _old_path_is_synchronous():
    from poise.application.work import WorkTools

    entered = Event()
    release = Event()
    foreground_progress = Event()

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

    runtime = _Runtime(None, foreground_progress)
    runtime.accounting = BlockingAccounting()
    progressed, outcome = _invoke_with_release(
        WorkTools(runtime), release, entered, foreground_progress, wait_for_progress=False
    )
    assert "error" not in outcome
    return not progressed


def test_slow_optional_processor_does_not_delay_or_replace_work_result():
    from poise.application.telemetry import OptionalTelemetry
    from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
    from poise.application.work import WorkTools

    entered = Event()
    release = Event()
    foreground_progress = Event()
    received = []

    def process(envelope):
        entered.set()
        release.wait()
        received.append(envelope)

    dispatcher = AsyncTelemetryDispatcher(process, max_pending=4)
    telemetry = OptionalTelemetry(_Clock(), dispatcher, "original-session")
    progressed, outcome = _invoke_with_release(
        WorkTools(_Runtime(telemetry, foreground_progress)),
        release,
        entered,
        foreground_progress,
        wait_for_progress=True,
    )
    dispatcher.close()

    assert progressed is True
    assert "error" not in outcome
    assert outcome["result"]["results"][0]["value"] == {"state": "authoritative"}
    assert len(received) == 1


def _configure_production_accounting(project):
    from conftest import write_json
    from tests.accounting.test_domain import policy
    from tests.runner.helpers import stage

    process = {
        "goal_type": "development",
        "benefit": {"git_categories": ["code", "documentation"], "sections": []},
        "route": {"entry": "write"},
        "stages": [
            stage(
                "write",
                "produce",
                {"complete": None},
                False,
                ["src/**", "tests/**", "docs/**"],
                ["write"],
            )
        ],
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    project["cfg"]["schema"] = "ddd-accounting-11"
    project["cfg"]["accounting"] = policy()
    project["cfg"]["automatic_checks"] = []
    write_json(project["root"] / "config/processes/development.json", process)
    write_json(project["config_path"], project["cfg"])
    task = deepcopy(project["task"])
    task["methods"] = []
    task["method_inputs"] = []
    task["checks"] = {"write": []}
    task["evidence_plan"] = {
        "write": {"subject_methods": {}, "arguments": [], "review_arguments": []}
    }
    return task


def _bootstrap_request(task):
    from tests.batch.helpers import message, request

    packet = request(
        "bootstrap",
        {"task": task, "decision": None, "feedback": None, "rework_stage": None},
        [message("turn-0082")],
    )
    packet["telemetry"] = _raw_telemetry()
    return packet


def _accounting_request(telemetry=None):
    from tests.batch.helpers import request

    packet = request(
        "show",
        {
            "queries": [
                {
                    "id": "economics",
                    "kind": "accounting",
                    "scope": {"kind": "all", "id": None},
                    "group_by": [],
                    "from": None,
                    "to": None,
                }
            ]
        },
    )
    if telemetry is not None:
        packet["telemetry"] = telemetry
    return packet


def test_production_worktools_envelope_persists_once_across_restart(project):
    from poise.application.telemetry import OptionalTelemetry
    from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
    from poise.runtime import Poise
    from poise.application.work import WorkTools

    captured = []

    class Capture:
        def submit(self, envelope):
            captured.append(envelope)
            return True

    task = _configure_production_accounting(project)
    runtime = Poise(project["config_path"], "original-session", clock=_Clock())
    assert isinstance(runtime.telemetry, OptionalTelemetry)
    assert isinstance(runtime.telemetry.dispatcher, AsyncTelemetryDispatcher)
    runtime.telemetry.dispatcher.close()
    runtime.telemetry.dispatcher = Capture()

    result = WorkTools(runtime).invoke(_bootstrap_request(task))
    assert result["status"] == "active"
    assert len(captured) == 1
    envelope = captured[0]
    data = envelope.data
    assert data["operation"] == "bootstrap"
    assert data["session"] == "original-session"
    expected_turn = runtime.interactions.prepare(
        [
            {
                "conversation_id": "conversation-A",
                "message_id": "turn-0082",
                "occurred_at": "2026-09-06T15:00:00+00:00",
                "reason": "initial",
                "subject": None,
            }
        ]
    )[0].identity
    assert data["turn_id"] == expected_turn
    assert data["before_binding"] == {
        "task": None,
        "sprint": None,
        "goal_type": None,
        "stage": None,
        "iteration": None,
    }
    assert data["after_binding"] == {
        "task": "T1",
        "sprint": None,
        "goal_type": "development",
        "stage": "write",
        "iteration": 1,
    }
    assert data["telemetry"] == _raw_telemetry()
    assert data["started"] == {
        "audit_utc": "2026-09-13T01:00:00+00:00",
        "monotonic_ns": 10,
        "comparison_domain": "boot-0082",
    }
    assert data["finished"] == {
        "audit_utc": "2026-09-13T00:59:00+00:00",
        "monotonic_ns": 2_000_010,
        "comparison_domain": "boot-0082",
    }
    assert "elapsed" not in data and "duration" not in data

    first = AsyncTelemetryDispatcher(runtime.accounting.port.process, max_pending=4)
    assert first.submit(envelope) is True
    assert first.submit(envelope) is True
    first.close()

    restarted = Poise(project["config_path"], "restart-session", clock=_Clock())
    restarted.telemetry.dispatcher.close()
    second = AsyncTelemetryDispatcher(restarted.accounting.port.process, max_pending=4)
    assert second.submit(envelope) is True
    second.close()

    stored = restarted.accounting.port.repo.snapshot()["telemetry"]
    assert len(stored) == 1
    stored_data = json.loads(stored[0]["data"])
    assert stored_data == data
    assert "elapsed" not in stored_data and "duration" not in stored_data

    report = WorkTools(restarted).invoke(_accounting_request())["results"][0]["value"]
    assert report["totals"]["model_tokens"] == 8
    assert report["totals"]["active_seconds"] == 2.0


def test_downstream_failure_is_visible_as_partial_only_in_public_report(project):
    from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
    from poise.runtime import Poise
    from poise.application.work import WorkTools

    task = _configure_production_accounting(project)
    runtime = Poise(project["config_path"], "failure-session", clock=_Clock())
    runtime.telemetry.dispatcher.close()
    captured_failure = Event()

    def fail(_envelope):
        captured_failure.set()
        raise RuntimeError("optional sink failed")

    dispatcher = AsyncTelemetryDispatcher(fail, max_pending=4)
    runtime.telemetry.dispatcher = dispatcher
    work_result = WorkTools(runtime).invoke(_bootstrap_request(task))
    assert work_result["status"] == "active"
    assert captured_failure.wait(5)
    dispatcher.close()

    report = WorkTools(runtime).invoke(_accounting_request())["results"][0]["value"]
    assert report["telemetry"]["coverage"] == "partial"
    assert report["telemetry"]["failed"] == 1


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
    import pytest

    selected = pytest.main(
        [str(Path(__file__).resolve()), "-q", "-k", "not canonical_documentation"]
    )
    if selected != pytest.ExitCode.OK:
        return int(selected)
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
