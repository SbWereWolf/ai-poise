"""Best-effort capture of work telemetry without owning the work outcome."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

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
    return {
        "task": task["id"],
        "sprint": task["sprint_id"],
        "goal_type": task["contract"]["goal_type"],
        "stage": task["process"]["stages"][task["stage_index"]]["id"],
        "iteration": task["iteration"],
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
