"""Real filesystem/queue/SQLite failures preserve main output and durable recovery."""
from copy import deepcopy
import json
import sqlite3
import time

from tests.accounting.agent_metrics_support import literal
from tests.accounting.test_agent_metrics_pipeline import open_scene, total_values
from tests.hook_transport.architecture_support import write


def settled(scene):
    scene.native("Stop")
    reply = scene.wait_query(family="usage-current")
    assert total_values(reply) == [150]
    scene.wait_status(lambda code, data: code == 0 and data["delivery"]["pending"] == 0)


def assert_actual_failed_tool_is_preserved(scene, call_id):
    actual = scene.execute_tool(call_id, exit_code=7)
    assert actual["exit_code"] == 7
    assert actual["stdout"] == "А\n".encode()
    assert actual["stderr"] == b""
    assert actual["native"] == {}, "F16_OPTIONAL_FAILURE_CHANGED_NATIVE_RESPONSE"
    assert (scene.parent / "actual-tool-effects.txt").read_text() == "effect\n"


def test_unavailable_source_preserves_tool_result_and_prior_observed_usage(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        settled(scene)
        (scene.sources / "session.jsonl").unlink()
        assert_actual_failed_tool_is_preserved(scene, "f16-missing-source")
        _, status = scene.wait_status(lambda code, data: data["coverage"]["state"] == "partial")
        diagnostic = json.dumps(status)
        assert "source" in diagnostic and status["continuation"]
        assert total_values(scene.query(family="usage-current")) == [150]
    finally:
        scene.close()


def test_unwritable_spool_is_diagnostic_without_replacing_main_result(project, tmp_path):
    scene = open_scene(project, tmp_path)
    saved = scene.metrics / "spool-owned-test-fixture"
    queue = scene.metrics / "spool"
    try:
        settled(scene)
        queue.rename(saved)
        queue.write_bytes(b"fixture-obstructed-spool\n")
        assert_actual_failed_tool_is_preserved(scene, "f16-unwritable-spool")
        code, status = scene.wait_status(lambda result, data: result == 2 and data["status"] == "unavailable")
        assert code == 2 and status["continuation"]
        assert str(queue) in json.dumps(status)
        assert queue.read_bytes() == b"fixture-obstructed-spool\n"
        assert total_values(scene.query(family="usage-current")) == [150]
    finally:
        if queue.is_file() and queue.read_bytes() == b"fixture-obstructed-spool\n":
            queue.unlink()
        if saved.exists() and not queue.exists():
            saved.rename(queue)
        scene.close()


def test_held_real_optional_sqlite_write_then_replay_commits_new_facts(project, tmp_path):
    scene = open_scene(project, tmp_path)
    held = None
    try:
        settled(scene)
        held = sqlite3.connect(scene.metrics / "observed.sqlite", isolation_level=None)
        held.execute("BEGIN IMMEDIATE")
        with (scene.sources / "session.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(literal("additional-usage-event.json")) + "\n")
        assert_actual_failed_tool_is_preserved(scene, "f16-held-sqlite")
        _, status = scene.wait_status(lambda code, data: data["delivery"]["failed_attempts"] >= 1)
        assert status["delivery"]["pending"] >= 1
        assert status["continuation"]
        held.execute("ROLLBACK")
        held.close()
        held = None
        code, replay = scene.operation("replay")
        assert code == 0 and replay["status"] == "completed"
        assert replay["delivered"] >= 1 and replay["failed"] == 0 and replay["corrupt"] == 0
        final = scene.wait_query(family="usage-current", predicate=lambda data: total_values(data) == [20, 150])
        assert total_values(final) == literal("expected-additional-totals.json")
        before = final["metrics"]
        code, again = scene.operation("replay")
        assert code == 0 and again["delivered"] == 0
        assert scene.query(family="usage-current")["metrics"] == before
    finally:
        if held is not None:
            held.execute("ROLLBACK")
            held.close()
        scene.close()


def test_full_existing_queue_keeps_pending_record_and_main_result(project, tmp_path):
    from poise.application.telemetry import TelemetryEnvelope
    from poise.infrastructure.telemetry_spool import TelemetrySpool

    scene = open_scene(project, tmp_path)
    try:
        settled(scene)
        with sqlite3.connect(scene.metrics / "observed.sqlite") as db:
            stored = db.execute("SELECT data FROM accounting_cycles ORDER BY id").fetchall()
        candidates = [json.loads(row[0]) for row in stored]
        actual = next(row["envelope"] for row in candidates if row.get("kind") == "telemetry_envelope")
        changed = deepcopy(scene.policy)
        changed["delivery"]["policy"]["max_entries"] = 1
        changed["delivery"]["policy"]["batch_items"] = 1
        write(scene.policy_path, changed)
        fixture_time = time.time() + 3600

        def unavailable(_):
            raise OSError("test fixture receiver unavailable")

        # Arrange a legitimate saved retry which is not due yet. No private authorization patch.
        queue = TelemetrySpool(scene.metrics / "spool", changed["delivery"]["policy"],
                               unavailable, now=lambda: fixture_time)
        queue.enqueue(TelemetryEnvelope("f16-full-queue-fixture", actual))
        arrangement = queue.replay()
        assert arrangement == {"attempted": 1, "delivered": 0, "failed": 1, "corrupt": 0}
        assert_actual_failed_tool_is_preserved(scene, "f16-full-spool")
        _, status = scene.operation("status")
        assert status["delivery"]["pending"] == 1
        assert status["delivery"]["records"][0]["identity"] == "f16-full-queue-fixture"
        assert status["continuation"]
        assert total_values(scene.query(family="usage-current")) == [150]
    finally:
        write(scene.policy_path, scene.policy)
        scene.close()
