"""Retryable optional delivery must never control authoritative work."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import pytest

from conftest import write_json
from poise.application.telemetry import OptionalTelemetry, TelemetryEnvelope
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
from poise.runtime import Poise
from tests.accounting.test_domain import policy
from tests.accounting.test_optional_telemetry_isolation import (
    _Clock, _Runtime, _raw_telemetry, _request, _invoke_with_release,
)


def delivery_policy(**changes):
    return {"max_entries": 4, "max_bytes": 65536, "max_event_bytes": 8192,
            "max_attempts": 3, "batch_items": 2, "retry_seconds": 5,
            "lock_seconds": .05, "lock_poll_seconds": .005, **changes}


def envelope(key="one", **data):
    return TelemetryEnvelope(key, data or {"raw": 17})


def spool(tmp_path, process, now=lambda: 100, **limits):
    from poise.infrastructure.telemetry_spool import TelemetrySpool
    return TelemetrySpool(tmp_path/"spool", delivery_policy(**limits), process, now=now)


def test_delivery_composes_without_accessing_unavailable_storage(project):
    project["cfg"]["accounting"] = policy()
    write_json(project["config_path"], project["cfg"])
    # A regular file where the future directory would be. Composition must not open it.
    parent = project["root"] / "not-a-directory"
    parent.write_text("unavailable")
    project["cfg"]["telemetry_delivery"] = {
        "directory": str(parent/"queue"), "policy": delivery_policy()}
    write_json(project["config_path"], project["cfg"])
    runtime = Poise(project["config_path"], "delivery", clock=_Clock())
    assert runtime.telemetry_delivery is not None
    result = WorkTools(runtime).invoke(_request())
    assert result["status"] == "read_only"
    runtime.telemetry.flush()
    assert runtime.telemetry.summary()["failed"] == 1
    assert runtime.telemetry_delivery.status()["status"] == "unavailable"


def test_envelope_and_projection_are_atomic_on_sink_failure(project):
    project["cfg"]["accounting"] = policy()
    write_json(project["config_path"], project["cfg"])
    runtime = Poise(project["config_path"], "atomic", clock=_Clock())
    sink = runtime.accounting.port
    service = OptionalTelemetry(_Clock(), AsyncTelemetryDispatcher(sink.process, 4), "atomic")
    token = service.begin("show", None, _raw_telemetry(), None)
    assert service.finish(token, None, {"status": "read_only"})
    service.flush()
    rows = sink.telemetry_repo.snapshot()["telemetry"]
    original = TelemetryEnvelope(rows[0]["id"], json.loads(rows[0]["data"]))
    other = deepcopy(original.data)
    other["telemetry"]["usage"][0]["event_id"] = "usage-2"
    other["telemetry"]["usage"][0]["sequence"] = 2
    other["telemetry"]["intervals"] = [{
        "source": "test", "stream": "timer", "event_id": "bad-time",
        "started_at": "2026-09-13T00:59:00+00:00",
        "ended_at": "2026-09-13T01:00:00+00:00"}]
    # Preparation succeeds; an existing conflicting stored interval fails after usage insertion.
    bad = TelemetryEnvelope("conflict", other)
    with sink.telemetry_repo.database.transaction() as db:
        from poise.modules.accounting.domain import identity
        db.execute("INSERT INTO accounting_cycles VALUES(?,?,?,?,?,?,?)", (
            identity(["test", "timer", "bad-time"]), None, runtime.cfg["project"],
            "atomic", "2026-09-13T00:59:00+00:00", "2026-09-13T01:00:00+00:00",
            json.dumps({"kind": "reported", "event": {"conflict": True}})))
    # Make the explicit reported-mode setup valid without touching any primary Task data.
    raw_policy = deepcopy(runtime.cfg["accounting"])
    raw_policy["time_mode"] = "reported"
    from poise.modules.accounting.domain import MetricPolicy
    sink.policy = MetricPolicy.parse(raw_policy)
    sink.telemetry_repo.policy = sink.policy
    with pytest.raises(PoiseError, match="Time identity conflict"):
        sink.process(bad)
    with sink.telemetry_repo.database.read_transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM accounting_usage").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM accounting_cycles WHERE id='conflict'").fetchone()[0] == 0
    # The failing attempt did not poison idempotency; remove only the test's conflicting fixture.
    with sink.telemetry_repo.database.transaction() as db:
        db.execute("DELETE FROM accounting_cycles WHERE id=?", (
            identity(["test", "timer", "bad-time"]),))
    sink.process(bad)
    sink.process(bad)
    with sink.telemetry_repo.database.read_transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM accounting_usage").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM accounting_cycles WHERE id='conflict'").fetchone()[0] == 1


def test_restart_replays_due_item_without_rewriting_source(tmp_path):
    def fail(_): raise OSError("sink down")
    first = spool(tmp_path, fail)
    first(envelope())
    assert first.status()["pending"] == 1
    assert first.status()["failed_attempts"] == 1
    received = []
    second = spool(tmp_path, received.append, now=lambda: 104)
    assert second.replay()["attempted"] == 0
    third = spool(tmp_path, received.append, now=lambda: 105)
    assert third.replay()["delivered"] == 1
    assert [(e.identity, e.data) for e in received] == [("one", {"raw": 17})]
    assert third.status()["pending"] == 0


def test_same_identity_conflict_capacity_and_exhaustion_are_explicit(tmp_path):
    def fail(_): raise OSError("private contents must not be logged")
    service = spool(tmp_path, fail, max_entries=1, max_attempts=1, batch_items=1)
    service(envelope())
    assert service.enqueue(envelope()) == "duplicate"
    with pytest.raises(PoiseError, match="identity conflict"):
        service.enqueue(envelope(raw=18))
    with pytest.raises(PoiseError, match="capacity"):
        service.enqueue(envelope("two"))
    assert service.status()["exhausted"] == 1
    assert service.replay()["attempted"] == 0
    record = json.loads(next((tmp_path/"spool").glob("*.event.json")).read_text())
    assert record["last_error"] == "OSError"
    assert "private contents" not in json.dumps(record)


def test_crash_after_commit_before_ack_replays_idempotently(tmp_path):
    stored = {}
    def sink(e): stored.setdefault(e.identity, e.data)
    service = spool(tmp_path, sink)
    service.enqueue(envelope())
    # Simulate a committed sink call followed by worker death before queue acknowledgement.
    sink(envelope())
    restarted = spool(tmp_path, sink)
    assert restarted.replay()["delivered"] == 1
    assert stored == {"one": {"raw": 17}}


def test_worker_disk_and_sink_waits_do_not_block_work(tmp_path):
    entered, release, progress = Event(), Event(), Event()
    received = []
    def wait_sink(item):
        entered.set()
        release.wait()
        received.append(item)
    service = spool(tmp_path, wait_sink)
    dispatch = AsyncTelemetryDispatcher(service, 2)
    tools = WorkTools(_Runtime(OptionalTelemetry(_Clock(), dispatch, "s"), progress))
    progressed, outcome = _invoke_with_release(
        tools, release, entered, progress, wait_for_progress=True)
    dispatch.close()
    assert progressed
    assert outcome["result"]["results"][0]["value"] == {"state": "authoritative"}
    assert len(received) == 1


def test_bad_queue_file_does_not_destroy_other_items(tmp_path):
    received = []
    service = spool(tmp_path, received.append)
    service.enqueue(envelope())
    (tmp_path/"spool"/"bad.event.json").write_text("not-json")
    assert service.status()["corrupt"] == 1
    report = service.replay()
    assert report["delivered"] == 1
    assert report["corrupt"] == 1
    assert (tmp_path/"spool"/"bad.event.json").exists()


def test_bounds_apply_to_events_bytes_and_drain_batch(tmp_path):
    service = spool(tmp_path, lambda _: None)
    with pytest.raises(PoiseError, match="event byte"):
        service.enqueue(envelope(huge="z"*8192))
    for key in ("a", "b", "c"):
        service.enqueue(envelope(key))
    assert service.replay()["attempted"] == 2
    assert service.status()["pending"] == 1


def test_symlink_queue_entry_never_reads_external_payload(tmp_path):
    service = spool(tmp_path, lambda _: pytest.fail("must not deliver external data"))
    service.enqueue(envelope())
    item = next((tmp_path/"spool").glob("*.event.json"))
    external = tmp_path/"external"
    external.write_bytes(item.read_bytes())
    item.unlink()
    item.symlink_to(external)
    assert service.replay()["corrupt"] == 1
    assert external.exists()


def test_inflight_counts_toward_dispatch_bound():
    entered, release = Event(), Event()
    def blocking(_):
        entered.set()
        release.wait()
    dispatch = AsyncTelemetryDispatcher(blocking, 1)
    assert dispatch.submit(envelope())
    assert entered.wait(5)
    try:
        assert dispatch.submit(envelope('two')) is False
    finally:
        release.set()
        dispatch.close()
    assert dispatch.summary()['dropped'] == 1


def test_dispatch_dedup_memory_is_bounded():
    dispatch = AsyncTelemetryDispatcher(lambda _: None, 2)
    for n in range(20):
        assert dispatch.submit(envelope(str(n)))
        dispatch.flush()
    assert len(dispatch._seen) <= 4
    dispatch.close()


def test_slow_queue_persistence_is_not_on_work_path(tmp_path, monkeypatch):
    entered, release, progress = Event(), Event(), Event()
    service = spool(tmp_path, lambda _: None)
    original = service.enqueue
    def blocked(item):
        entered.set()
        release.wait()
        return original(item)
    monkeypatch.setattr(service, 'enqueue', blocked)
    dispatch = AsyncTelemetryDispatcher(service, 2)
    tools = WorkTools(_Runtime(OptionalTelemetry(_Clock(), dispatch, 's'), progress))
    progressed, outcome = _invoke_with_release(
        tools, release, entered, progress, wait_for_progress=True)
    dispatch.close()
    assert progressed
    assert 'error' not in outcome


def test_operator_cli_is_explicit_and_read_only_until_replay(project, tmp_path):
    import subprocess
    import sys
    project['cfg']['telemetry_delivery'] = {
        'directory': str(tmp_path/'operator-queue'), 'policy': delivery_policy()}
    write_json(project['config_path'], project['cfg'])
    script = Path(__file__).resolve().parents[2]/'tools/telemetry_delivery.py'
    result = subprocess.run([sys.executable, '-B', str(script), '--config',
        str(project['config_path']), 'status'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['pending'] == 0
    assert not (tmp_path/'operator-queue').exists()


def test_two_consumers_do_not_concurrently_deliver_the_same_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    entered, release = Event(), Event()
    delivered = []
    def blocked(item):
        entered.set()
        release.wait()
        delivered.append(item.identity)
    first = spool(tmp_path, blocked)
    first.enqueue(envelope())
    second = spool(tmp_path, lambda _: pytest.fail('second consumer must not enter sink'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.replay)
        assert entered.wait(5)
        try:
            with pytest.raises(PoiseError, match='lock'):
                second.replay()
        finally:
            release.set()
        assert future.result()['delivered'] == 1
    assert delivered == ['one']


def test_production_dispatch_spools_failed_sink_and_restart_commits_once(project, tmp_path):
    project['cfg']['accounting'] = policy()
    project['cfg']['telemetry_delivery'] = {
        'directory': str(tmp_path/'live-queue'), 'policy': delivery_policy()}
    write_json(project['config_path'], project['cfg'])
    runtime = Poise(project['config_path'], 'spooled', clock=_Clock())
    runtime.telemetry_delivery.now = lambda: 100
    def fail(_): raise OSError('optional database offline')
    runtime.telemetry_delivery.process = fail
    outcome = WorkTools(runtime).invoke(_request())
    assert outcome['status'] == 'read_only'
    runtime.telemetry.flush()
    assert runtime.telemetry_delivery.status()['pending'] == 1
    restarted = Poise(project['config_path'], 'spooled-restart', clock=_Clock())
    restarted.telemetry_delivery.now = lambda: 105
    assert restarted.telemetry_delivery.replay()['delivered'] == 1
    assert restarted.telemetry_delivery.replay()['attempted'] == 0
    with restarted.accounting.port.telemetry_repo.database.read_transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM accounting_usage').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM accounting_cycles').fetchone()[0] == 1
    with restarted.store.database.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM accounting_usage').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM accounting_cycles').fetchone()[0] == 0
