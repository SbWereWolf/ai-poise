from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys

import pytest

from poise.common import PoiseError
from poise.runtime import Poise
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
            raise PoiseError("FakeClock exhausted") from exc


def observation(audit_utc: str, monotonic_ns: int, domain: str = "boot-a"):
    from poise.modules.accounting.clock import ClockObservation

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


def test_poise_clock_boundary_is_explicit_and_runtime_has_no_wall_fallback():
    from poise.infrastructure.clock import SystemClock
    from poise.infrastructure.accounting import RuntimeAccounting
    from poise.modules.accounting.clock import ClockObservation

    assert "clock" in inspect.signature(Poise).parameters
    assert "datetime.now" not in inspect.getsource(RuntimeAccounting)
    clock = SystemClock()
    first = clock.observe()
    second = clock.observe()
    assert isinstance(first, ClockObservation)
    assert datetime.fromisoformat(first.audit_utc).utcoffset() == timedelta(0)
    assert first.comparison_domain
    assert second.comparison_domain == first.comparison_domain
    assert second.monotonic_ns >= first.monotonic_ns


def test_system_clock_rejects_missing_stable_boot_identity(monkeypatch):
    from poise.infrastructure import clock as clock_module

    def missing(*args, **kwargs):
        raise OSError("boot identity unavailable")

    monkeypatch.setattr(clock_module.Path, "read_text", missing)
    with pytest.raises(PoiseError, match="boot identity.*readable"):
        clock_module.SystemClock()


def test_tool_cycle_uses_monotonic_duration_and_preserves_backwards_audit_utc(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 1_000_000_000),
        observation("2026-09-11T09:59:00+00:00", 2_000_000_000),
        observation("2026-09-11T09:58:00+00:00", 3_000_000_000),
        observation("2026-09-11T09:57:00+00:00", 61_000_000_000),
    ])
    h, tools, _ = setup(project, clock)

    tools.invoke(request("cancel", {"reason": "clock test complete"}))
    h.telemetry.flush()
    rows = h.accounting.port.repo.snapshot()["telemetry"]
    assert len(rows) == 2
    first, last = (json.loads(row["data"]) for row in rows)
    assert first["started"]["audit_utc"] == "2026-09-11T10:00:00+00:00"
    assert last["finished"] == {
        "audit_utc": "2026-09-11T09:57:00+00:00",
        "monotonic_ns": 61_000_000_000,
        "comparison_domain": "boot-a",
    }
    assert "seconds" not in first and "elapsed" not in last
    assert metrics(tools)["totals"]["active_seconds"] == 59


def test_same_domain_backwards_monotonic_value_is_rejected_without_clamping(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 2_000_000_000),
        observation("2026-09-11T10:00:01+00:00", 1_000_000_000),
    ])

    h, tools, result = setup(project, clock)
    assert result["status"] == "active"
    h.telemetry.flush()
    report = metrics(tools)
    assert report["totals"]["active_seconds"] is None
    assert report["totals"]["time_coverage"] == "partial"


def test_malformed_persisted_monotonic_order_is_rejected_without_mutation(project):
    clock = FakeClock([
        observation("2026-09-11T10:00:00+00:00", 1_000),
        observation("2026-09-11T10:00:01+00:00", 2_000),
    ])
    h, _, _ = setup(project, clock)
    h.telemetry.flush()
    h.accounting.port.repo.start(h.session,h.current_task(),observation("2026-09-11T10:00:00+00:00",1_000),None)
    row, data = cycle(h)
    data["timing"]["started_monotonic_ns"] = 10_000
    data["timing"]["last_monotonic_ns"] = 5_000
    with h.store.transaction() as db:
        db.execute("UPDATE accounting_cycles SET data=? WHERE id=?", (json.dumps(data), row["id"]))

    with pytest.raises(PoiseError, match="Persisted monotonic clock state is backwards"):
        h.accounting.port.repo.stop(
            h.session,
            observation("2026-09-11T10:00:02+00:00", 6_000),
        )

    unchanged_row, unchanged_data = cycle(h)
    assert unchanged_row["ended_at"] is None
    assert unchanged_data == data


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
    h.telemetry.flush()
    h.accounting.port.repo.start(h.session,h.current_task(),observation("2026-09-11T10:00:00+00:00",1_000_000_000),None)
    row, data = cycle(h)
    with h.store.transaction() as db:
        if legacy:
            data.pop("timing")
        else:
            data["timing"]["comparison_domain"] = "boot-old"
        db.execute("UPDATE accounting_cycles SET data=? WHERE id=?", (json.dumps(data), row["id"]))

    with pytest.raises(PoiseError, match="cannot be compared.*retry"):
        h.accounting.port.repo.touch(
            h.session,
            observation("2026-09-11T11:00:00+00:00",3_000_000_000,"boot-b"),
        )

    old_row, old_data = cycle(h)
    assert old_row["ended_at"] == "2026-09-11T11:00:00+00:00"
    assert "seconds" not in old_data
    assert old_data["timing"]["status"] == "unmeasured_clock_discontinuity"
    assert old_data["timing"]["elapsed_microseconds"] is None

    h.accounting.port.repo.start(
        h.session,h.current_task(),observation("2026-09-11T11:00:01+00:00",4_000_000_000,"boot-b"),None
    )
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
    h.telemetry.flush()
    h.accounting.port.repo.start(h.session,h.current_task(),observation("2026-09-11T10:00:00+00:00",1_000_000_000),None)
    h.accounting.port.repo.stop(h.session,observation("2026-09-11T10:00:05+00:00",6_000_000_000))
    row, data = cycle(h)
    data.pop("timing")
    data["seconds"] = 12.5
    with h.store.transaction() as db:
        db.execute("UPDATE accounting_cycles SET data=? WHERE id=?", (json.dumps(data), row["id"]))

    assert metrics(tools)["totals"]["active_seconds"] == 13.5
    _, stored = cycle(h)
    assert "timing" not in stored


def test_public_work_interface_supports_fake_clock_in_a_subprocess(project):
    source = r'''
import io
import json
import sys
from poise.common import PoiseError
from poise.interfaces.work import execute
from poise.modules.accounting.clock import ClockObservation
from poise.runtime import Poise

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
            raise PoiseError("FakeClock exhausted") from exc

clock = FakeClock()
runtime = Poise(sys.argv[1], "clock-subprocess", clock=clock)
packet = {"operation":"bootstrap","input":{"task":None,"decision":None,"feedback":None,"rework_stage":None},"messages":[]}
code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), sys.stdout)
assert code == 0
try:
    clock.observe()
except PoiseError as exc:
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
