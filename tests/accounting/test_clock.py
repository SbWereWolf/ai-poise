from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys

import pytest

from harness.common import HarnessError
from harness.runtime import Harness
from tests.batch.helpers import request
from .test_paths import metrics, setup


class FakeClock:
    """Finite deterministic clock: exhausted input is an error, never a fallback."""

    def __init__(self, observations):
        self._observations = iter(observations)

    def observe(self):
        try:
            return next(self._observations)
        except StopIteration as exc:
            raise HarnessError("FakeClock exhausted") from exc


def observation(audit_utc: str, monotonic_ns: int, domain: str = "boot-a"):
    from harness.modules.accounting.clock import ClockObservation

    return ClockObservation(
        audit_utc=audit_utc,
        monotonic_ns=monotonic_ns,
        comparison_domain=domain,
    )


def cycle(h):
    rows = h.accounting.port.repo.snapshot()["cycles"]
    assert len(rows) == 1
    row = rows[0]
    return row, json.loads(row["data"])


def test_harness_clock_boundary_is_explicit_and_runtime_has_no_wall_fallback():
    from harness.infrastructure.clock import SystemClock
    from harness.infrastructure.accounting import RuntimeAccounting
    from harness.modules.accounting.clock import ClockObservation

    assert "clock" in inspect.signature(Harness).parameters
    assert "datetime.now" not in inspect.getsource(RuntimeAccounting)
    clock = SystemClock()
    first = clock.observe()
    second = clock.observe()
    assert isinstance(first, ClockObservation)
    assert datetime.fromisoformat(first.audit_utc).utcoffset() == timedelta(0)
    assert first.comparison_domain
    assert second.comparison_domain == first.comparison_domain
    assert second.monotonic_ns >= first.monotonic_ns


def test_tool_cycle_uses_monotonic_duration_and_preserves_backwards_audit_utc(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 1_000_000_000),
        observation("2026-09-11T09:59:00+00:00", 2_000_000_000),
        observation("2026-09-11T09:58:00+00:00", 3_000_000_000),
        observation("2026-09-11T09:57:00+00:00", 61_000_000_000),
    ])
    h, tools, _ = setup(project, clock)

    tools.invoke(request("cancel", {"reason": "clock test complete"}))

    row, data = cycle(h)
    assert row["started_at"] == "2026-09-11T10:00:00+00:00"
    assert row["ended_at"] == "2026-09-11T09:57:00+00:00"
    assert data["timing"] == {
        "version": 2,
        "comparison_domain": "boot-a",
        "started_monotonic_ns": 1_000_000_000,
        "last_monotonic_ns": 61_000_000_000,
        "ended_monotonic_ns": 61_000_000_000,
        "elapsed_microseconds": 60_000_000,
        "status": "measured",
    }
    assert "seconds" not in data


def test_same_domain_backwards_monotonic_value_is_rejected_without_clamping(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 2_000_000_000),
        observation("2026-09-11T10:00:01+00:00", 1_000_000_000),
    ])

    with pytest.raises(HarnessError, match="Monotonic clock moved backwards"):
        setup(project, clock)

    h = Harness(project["config_path"], "A", clock=FakeClock([]))
    row, data = cycle(h)
    assert row["ended_at"] is None
    assert data["timing"]["started_monotonic_ns"] == 2_000_000_000
    assert data["timing"]["last_monotonic_ns"] == 2_000_000_000
    assert data["timing"]["elapsed_microseconds"] is None


@pytest.mark.parametrize("legacy", [False, True], ids=["other-boot", "legacy-open"])
def test_incomparable_open_cycle_is_recorded_unmeasured_then_retryable(project, legacy):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 1_000_000_000),
        observation("2026-09-11T10:00:01+00:00", 2_000_000_000),
        observation("2026-09-11T11:00:00+00:00", 3_000_000_000, "boot-b"),
        observation("2026-09-11T11:00:01+00:00", 4_000_000_000, "boot-b"),
        observation("2026-09-11T11:00:02+00:00", 5_000_000_000, "boot-b"),
    ])
    h, tools, _ = setup(project, clock)
    row, data = cycle(h)
    with h.store.transaction() as db:
        if legacy:
            data.pop("timing")
        else:
            data["timing"]["comparison_domain"] = "boot-old"
        db.execute("UPDATE accounting_cycles SET data=? WHERE id=?", (json.dumps(data), row["id"]))

    with pytest.raises(HarnessError, match="cannot be compared.*retry"):
        tools.invoke(request("show", {"queries": [{"id": "state", "kind": "task"}]}))

    old_row, old_data = cycle(h)
    assert old_row["ended_at"] == "2026-09-11T11:00:00+00:00"
    assert "seconds" not in old_data
    assert old_data["timing"]["status"] == "unmeasured_clock_discontinuity"
    assert old_data["timing"]["elapsed_microseconds"] is None

    result = tools.invoke(request("show", {"queries": [{"id": "state", "kind": "task"}]}))
    assert result["status"] == "read_only"
    rows = h.accounting.port.repo.snapshot()["cycles"]
    assert len(rows) == 2
    assert sum(row["ended_at"] is None for row in rows) == 1


def test_closed_legacy_duration_remains_readable_without_migration(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 1_000_000_000),
        observation("2026-09-11T10:00:01+00:00", 2_000_000_000),
        observation("2026-09-11T10:00:02+00:00", 3_000_000_000),
        observation("2026-09-11T10:00:03+00:00", 4_000_000_000),
        observation("2026-09-11T10:00:04+00:00", 5_000_000_000),
        observation("2026-09-11T10:00:05+00:00", 6_000_000_000),
    ])
    h, tools, _ = setup(project, clock)
    tools.invoke(request("cancel", {"reason": "close fixture"}))
    row, data = cycle(h)
    data.pop("timing")
    data["seconds"] = 12.5
    with h.store.transaction() as db:
        db.execute("UPDATE accounting_cycles SET data=? WHERE id=?", (json.dumps(data), row["id"]))

    assert metrics(tools)["totals"]["active_seconds"] == 12.5
    _, stored = cycle(h)
    assert "timing" not in stored


def test_public_work_interface_supports_fake_clock_in_a_subprocess(project):
    source = r'''
import io
import json
import sys
from harness.common import HarnessError
from harness.interfaces.work import execute
from harness.modules.accounting.clock import ClockObservation
from harness.runtime import Harness

class FakeClock:
    def __init__(self):
        self.values = iter([
            ClockObservation("2026-09-11T10:00:00+00:00", 1_000_000_000, "subprocess-boot"),
            ClockObservation("2026-09-11T09:00:00+00:00", 2_000_000_000, "subprocess-boot"),
        ])
    def observe(self):
        try:
            return next(self.values)
        except StopIteration as exc:
            raise HarnessError("FakeClock exhausted") from exc

clock = FakeClock()
runtime = Harness(sys.argv[1], "clock-subprocess", clock=clock)
packet = {"operation":"bootstrap","input":{"task":None,"decision":None,"feedback":None,"rework_stage":None},"messages":[]}
code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), sys.stdout)
assert code == 0
try:
    clock.observe()
except HarnessError as exc:
    assert str(exc) == "FakeClock exhausted"
else:
    raise AssertionError("FakeClock retained an unexpected fallback observation")
print("fake-clock-subprocess-ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", source, str(project["config_path"])],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"status":"read_only"' in completed.stdout
    assert "fake-clock-subprocess-ok" in completed.stdout
