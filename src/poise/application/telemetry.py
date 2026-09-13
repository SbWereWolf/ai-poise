"""Best-effort capture of work telemetry without owning the work outcome."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import Event, Thread

from ..modules.accounting.clock import ClockObservation
from ..modules.accounting.domain import identity


def _binding(task):
    if task is None:
        return {
            "task": None,
            "sprint": None,
            "goal_type": None,
            "stage": None,
            "iteration": None,
        }
    contract = task.get("contract")
    process = task.get("process")
    stage_index = task.get("stage_index")
    stage = None
    if (
        isinstance(process, dict)
        and isinstance(process.get("stages"), list)
        and type(stage_index) is int
        and 0 <= stage_index < len(process["stages"])
    ):
        stage = process["stages"][stage_index]["id"]
    return {
        "task": task["id"],
        "sprint": task.get("sprint_id"),
        "goal_type": (
            contract.get("goal_type")
            if isinstance(contract, dict)
            else task.get("goal_type")
        ),
        "stage": stage,
        "iteration": task.get("iteration"),
    }


def _observation(value):
    if not isinstance(value, ClockObservation):
        raise TypeError("Clock.observe must return ClockObservation")
    return {
        "audit_utc": value.audit_utc,
        "monotonic_ns": value.monotonic_ns,
        "comparison_domain": value.comparison_domain,
    }


@dataclass(frozen=True)
class TelemetryEnvelope:
    identity: str
    data: dict


class _ThreadCapture:
    def __init__(self, work):
        self.finished = Event()
        self.final = None
        Thread(target=work, args=(self,), daemon=True).start()

    def complete(self, final):
        self.final = final
        self.finished.set()


class OptionalTelemetry:
    """Capture immutable inputs; every failure remains telemetry-only."""

    def __init__(self, clock, dispatcher, session):
        self.clock = clock
        self.dispatcher = dispatcher
        self.session = session
        self.dropped = 0

    def begin(self, operation, before, telemetry, turn_id):
        try:
            return {
                "operation": operation,
                "session": self.session,
                "turn_id": turn_id,
                "before_binding": _binding(before),
                "telemetry": deepcopy(telemetry),
                "started": _observation(self.clock.observe()),
            }
        except Exception:
            self.dropped += 1
            return None

    def _base(self, operation, before, telemetry, turn_id):
        return {
            "operation": operation,
            "session": self.session,
            "turn_id": turn_id,
            "before_binding": _binding(before),
            "telemetry": deepcopy(telemetry),
        }

    def _envelope(self, base, started, finished, final):
        data = {
            **base,
            "started": _observation(started),
            "after_binding": final["after_binding"],
            "finished": _observation(finished),
            "result_status": final["result_status"],
        }
        return TelemetryEnvelope(identity=identity(data), data=data)

    def capture(self, operation, before, telemetry, turn_id):
        """Start capture without waiting for Clock or downstream processing."""
        try:
            base = self._base(operation, before, telemetry, turn_id)
            begin_capture = getattr(self.dispatcher, "begin_capture", None)
            if begin_capture is not None:
                return begin_capture(self.clock, base, self._envelope)

            def work(token):
                try:
                    started = self.clock.observe()
                    token.finished.wait()
                    envelope = self._envelope(
                        base,
                        started,
                        self.clock.observe(),
                        token.final,
                    )
                    if not self.dispatcher.submit(envelope):
                        self.dropped += 1
                except Exception:
                    self.dropped += 1

            return _ThreadCapture(work)
        except Exception:
            self.dropped += 1
            return None

    def complete(self, token, after, result):
        if token is None:
            return False
        final = {
            "after_binding": _binding(after),
            "result_status": result.get("status"),
        }
        try:
            finish_capture = getattr(self.dispatcher, "finish_capture", None)
            if finish_capture is not None:
                return bool(finish_capture(token, final))
            token.complete(final)
            return True
        except Exception:
            self.dropped += 1
            return False

    def finish(self, token, after, result):
        if token is None:
            return False
        try:
            data = {
                **token,
                "after_binding": _binding(after),
                "finished": _observation(self.clock.observe()),
                "result_status": result.get("status"),
            }
            accepted = self.dispatcher.submit(
                TelemetryEnvelope(identity=identity(data), data=data)
            )
            if not accepted:
                self.dropped += 1
            return bool(accepted)
        except Exception:
            self.dropped += 1
            return False

    def flush(self):
        try:
            self.dispatcher.flush()
        except Exception:
            self.dropped += 1

    def summary(self):
        try:
            result = self.dispatcher.summary()
        except Exception:
            result = {
                "coverage": "partial",
                "submitted": 0,
                "processed": 0,
                "duplicates": 0,
                "failed": 0,
                "dropped": 0,
            }
        result = dict(result)
        result["dropped"] = result.get("dropped", 0) + self.dropped
        if result["dropped"] or result.get("submitted", 0):
            result["coverage"] = "partial"
        else:
            result["coverage"] = "unavailable"
        return result
