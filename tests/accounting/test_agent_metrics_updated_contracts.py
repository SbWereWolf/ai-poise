"""Source-bound delta contracts; healthy observations are never forced RED."""
import json
import os
import select
import subprocess
import sys
import time

from tests.accounting.agent_metrics_support import (
    FIXTURES, MetricsScene, ProcessorGate, assert_measurements, committed_observations, literal,
    replace_tokens,
)


def observed_stop(scene, trace):
    """Wait only actual captured worker PIDs; empty selection is observed directly."""
    import psutil

    scene.native("Stop", trace_directory=trace)
    records = lambda: [json.loads(line) for path in trace.glob("*.jsonl")
                       for line in path.read_text().splitlines()]
    captures = [row for row in records() if row["boundary"] == "actual-capture-return"]
    assert len(captures) == 1, "F16_ACTUAL_CAPTURE_RETURN_NOT_OBSERVED"
    captured = captures[0]
    if captured["jobs"] is None:
        raise AssertionError("F16_CAPTURE_REFUSED_BEFORE_JOB_PREPARATION")
    deadline = time.monotonic() + 15
    workers = []
    while captured["jobs"] and not workers and time.monotonic() < deadline:
        workers = [row for row in records() if row["boundary"] == "actual-detached-entry"]
        if not workers:
            time.sleep(.01)
    if captured["jobs"]:
        assert workers, "F16_ACTUAL_DETACHED_ENTRY_NOT_OBSERVED"
    for worker in workers:
        while time.monotonic() < deadline:
            try:
                process = psutil.Process(worker["pid"])
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(.01)
        else:
            raise AssertionError(("F16_OWN_CAPTURE_WORKER_NOT_TERMINAL", worker))
    scene.record({"kind": "actual-stop-capture-terminal", "capture": captured, "workers": workers})
    return captured


def test_stop_only_selection_populated_empty_missing_restored(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    original = source.read_bytes()
    # Only Stop owns collection; SessionStart/End retain their actual native owners.
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    observations = []
    try:
        scene.native("SessionStart")
        first = observed_stop(scene, tmp_path / "trace-populated")
        assert first["jobs"] > 0
        initial = scene.query(family="usage-current")
        assert_measurements(initial, literal("expected-usage-complete.json"))
        assert initial["coverage"]["complete_for_registered_scope"] is True
        overlap = scene.query(family="usage-current", scope={"kind": "source", "id": "codex"})
        assert overlap["metrics"] == [], "F16_OVERLAPPING_NATIVE_SOURCE_OWNS_DUPLICATE_USAGE"
        assert overlap["coverage"]["errors"] == []
        for state in ("empty", "missing", "restored"):
            if state == "empty":
                source.unlink()
            elif state == "missing":
                scene.sources.rmdir()
            else:
                scene.sources.mkdir()
                source.write_bytes(original)
            capture = observed_stop(scene, tmp_path / ("trace-" + state))
            query = scene.query(family="usage-current")
            assert_measurements(query, literal("expected-usage-complete.json"))
            observations.append({"state": state, "capture": capture, "coverage": query["coverage"]})
            scene.record({"kind": "selection-current-observation", **observations[-1]})
        # Gather every transition before declaring the exact reached source-selection RED.
        missing = [row["state"] for row in observations if row["capture"]["jobs"] == 0]
        if missing:
            raise AssertionError("F16_EMPTY_SELECTION_HAS_NO_CURRENT_OBSERVATION:" + ",".join(missing))
        for row in observations[:2]:
            assert row["coverage"]["complete_for_registered_scope"] is False, (
                "F16_STALE_COMPLETE_SOURCE_SELECTION", row)
            assert row["coverage"]["errors"], row
        assert observations[2]["coverage"]["complete_for_registered_scope"] is True, (
            "F16_RESTORED_SELECTION_RETAINS_STALE_FAILURE", observations[2])
        assert source.read_bytes() == original
        assert not any(call.get("event", {}).get("hook_event_name") == "PostToolUse"
                       for call in scene.calls)
    finally:
        if not scene.sources.exists():
            scene.sources.mkdir()
        if not source.exists():
            source.write_bytes(original)
        scene.close()


def test_dedicated_source_is_not_reported_missing_by_overlapping_selection(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    adoption = scene.sources / "adoption.jsonl"
    adoption.write_text(json.dumps(literal("adoption-event.json")) + "\n")
    original = adoption.read_bytes()
    scene.add_source(literal("adoption-source.json"))
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    try:
        scene.native("SessionStart")
        capture = observed_stop(scene, tmp_path / "trace-dedicated")
        assert capture["jobs"] > 0
        usage = scene.query(family="usage-current")
        assert_measurements(usage, literal("expected-usage-complete.json"))
        adopted = scene.query(family="hook-adoption")
        assert_measurements(adopted, literal("expected-adoption-measurements.json"))
        assert adopted["coverage"]["errors"] == []
        assert adopted["coverage"]["complete_for_registered_scope"] is True
        overlap = scene.query(family="usage-current", scope={"kind": "source", "id": "codex"})
        assert overlap["metrics"] == []
        assert overlap["coverage"]["errors"] == []
        code, health = scene.operation("status")
        assert code == 0
        assert health["delivery"]["pending"] == health["delivery"]["failed_attempts"] == 0
        assert adoption.read_bytes() == original
    finally:
        scene.close()


def test_unfinished_turn_keeps_duration_unknown(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source("unfinished-conflict")
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        timing = scene.wait_query(family="turn-timing-context")
        assert_measurements(timing, literal("expected-unfinished-duration.json"))
        usage = scene.wait_query(family="usage-current")
        assert_measurements(usage, literal("expected-usage-conflict.json"))
        assert source.read_bytes() == original
    finally:
        scene.close()




def test_existing_store_query_during_held_optional_writer(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.install()
    holder = None
    gate = ProcessorGate(tmp_path)
    try:
        scene.native("SessionStart", gate=gate.configuration)
        scene.native("Stop", gate=gate.configuration)
        scene.native("SessionEnd", gate=gate.configuration)
        deadline = time.monotonic() + 10
        while len(list(gate.root.glob("*.entered.json"))) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(list(gate.root.glob("*.entered.json"))) == 3, "F16_INITIAL_CONSUMERS_NOT_REACHED"
        gate.release()
        gate.wait_terminal()
        scene.wait_measurements("usage-current", literal("expected-usage-complete.json"))
        # All capture clients ended; the subject query below is issued exactly once.
        before = committed_observations(scene.metrics / "observed.sqlite")
        bounds = scene.policy["delivery"]["policy"]
        request = literal("held-writer-request.json")
        request.update(database=str(scene.metrics / "observed.sqlite"),
                       lock=str(scene.metrics / "observed.lock"))
        assert request["wait"] == bounds["lock_seconds"] == .05
        assert request["poll"] == bounds["lock_poll_seconds"] == .005
        holder = subprocess.Popen([sys.executable, "-B", str(FIXTURES / "held_writer.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=scene.general.env)
        holder.stdin.write(json.dumps(request) + "\n")
        holder.stdin.flush()
        assert select.select([holder.stdout], [], [], 10)[0], "F16_WRITER_NOT_REACHED"
        entered = json.loads(holder.stdout.readline())
        scene.record({"kind": "held-writer-entered", **entered})
        assert entered["pid"] == holder.pid and holder.pid != os.getpid()
        assert entered["in_transaction"] is True
        assert entered["boundary"] == "actual-reserved-writer-before-commit"
        assert holder.poll() is None
        trace = tmp_path / "held-query-sql.jsonl"
        reply = scene.query(family="usage-current", trace_sql=trace)
        assert holder.poll() is None, "F16_WRITER_NOT_HELD_DURING_QUERY"
        scene.record({"kind": "query-returned-with-writer-held", "holder_pid": holder.pid})
        assert_measurements(reply, literal("expected-usage-complete.json"))
        sql = [json.loads(line) for line in trace.read_text().splitlines()]
        connections = [row["database"] for row in sql if row["kind"] == "connection"]
        assert len(connections) == 1 and connections[0].endswith("?mode=ro")
        statements = [row["statement"].strip().upper() for row in sql if row["kind"] == "sql"]
        assert "PRAGMA USER_VERSION" in statements
        assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
                                             "ALTER", "REPLACE", "VACUUM", "BEGIN IMMEDIATE",
                                             "BEGIN EXCLUSIVE")) for statement in statements)
        holder.stdin.write("release\n")
        holder.stdin.flush()
        holder.stdin.close()
        holder.stdin = None
        stdout, stderr = holder.communicate(timeout=10)
        assert holder.returncode == 0 and stderr == "", (stdout, stderr)
        terminal = json.loads(stdout)
        assert terminal == {"pid": holder.pid, "boundary": "actual-writer-committed"}
        scene.record({"kind": "held-writer-terminal", **terminal})
        after = scene.query(family="usage-current")
        assert after["metrics"] == reply["metrics"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        gate.release()
        gate.wait_terminal()
        if holder is not None and holder.poll() is None:
            if holder.stdin is not None:
                holder.stdin.write("release\n")
                holder.stdin.flush()
                holder.stdin.close()
                holder.stdin = None
            holder.communicate(timeout=10)
        scene.close()


def test_initial_store_is_unavailable_until_real_first_commit(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    gate = ProcessorGate(tmp_path)
    gate.configuration = replace_tokens(literal("initialization-gate.json"), {"@GATE@": gate.root})
    try:
        scene.native("SessionStart")
        scene.native("Stop", gate=gate.configuration)
        entered = gate.wait_entered()
        assert entered[0]["in_transaction"] is True
        assert entered[0]["caller_function"] == "__init__"
        assert entered[0]["caller_class"] == "AccountingDatabase"
        assert (scene.metrics / "observed.sqlite").exists()
        # One real read while the actual first creation has not committed.
        request = literal("query.json")
        request["view"]["family"] = "usage-current"
        observed = scene.cli(request, trace_sql=tmp_path / "initial-read-sql.jsonl")
        scene.record({"kind": "real-initial-read-before-commit", "producer": entered,
                      "query": observed})
        gate.release()
        gate.wait_terminal()
        committed = scene.query(family="usage-current")
        assert_measurements(committed, literal("expected-usage-complete.json"))
        unavailable = json.loads(observed["stdout"])
        assert observed["exit_code"] == 2
        if unavailable["status"] != "unavailable":
            raise AssertionError("F16_INITIAL_COMMITTED_SNAPSHOT_AVAILABILITY:actual="
                                 + unavailable["status"] + ":" + unavailable["reason"])
        assert unavailable["reason"] and unavailable["continuation"]
    finally:
        gate.release()
        gate.wait_terminal()
        scene.close()
