"""Capture/source health uses real receipt time and preserves only message size/hash."""
from datetime import datetime, timezone
import hashlib

from tests.accounting.agent_metrics_support import MetricsScene, SESSION, assert_measurements, assert_metadata, literal, metadata_values


def test_delivery_health_matches_real_source_and_native_message_without_archiving_it(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    raw = source.read_bytes()
    stat = source.stat()
    # One first Stop-source job. Startup still crosses the real native lifecycle,
    # without introducing an earlier collection job into this counter oracle.
    scene.configure_metrics()
    for event in scene.definition["routes"]:
        if event != "Stop":
            scene.definition["routes"][event] = []
    scene.install(metrics=False)
    message = "PRIVATE_F16_ASSISTANT"
    before = datetime.now(timezone.utc)
    try:
        scene.native("SessionStart")
        scene.native("Stop", last_assistant_message=message)
        expected_hash = hashlib.sha256(message.encode()).hexdigest()
        reply = scene.wait_query(family="delivery-health", scope={"kind": "source", "id": "codex-transcript"}, predicate=lambda value:
                                  expected_hash in metadata_values(value, "last_assistant_message_sha256"))
        after = datetime.now(timezone.utc)
        assert_metadata(reply, {"session_id": SESSION, "turn_id": "turn-f16", "status": "processed",
                                "last_assistant_message_bytes": 21,
                                "last_assistant_message_sha256": expected_hash, "source_size": len(raw), "error": None})
        assert_measurements(reply, literal("expected-first-stop-health.json"))
        identities = metadata_values(reply, "source_identity")
        assert any(isinstance(item, dict) and item.get("device") == stat.st_dev
                   and item.get("inode") == stat.st_ino and item.get("mtime_ns") == stat.st_mtime_ns
                   for item in identities)
        for name in ("observed_at", "processed_at"):
            times = metadata_values(reply, name)
            assert times, ("F16_REAL_PROCESSING_TIME_MISSING", name)
            for text in times:
                actual = datetime.fromisoformat(text.replace("Z", "+00:00"))
                assert actual.tzinfo is not None and before <= actual <= after
        latency = [row for row in reply["metrics"] if row["name"] == "latency_ms"]
        assert latency
        for row in latency:
            assert type(row["value"]) in {int, float}
            assert 0 <= row["value"] <= (after - before).total_seconds() * 1000
            assert row["unit"] == "milliseconds" and row["kind"] == "observed"
            assert row["availability"]["state"] == "known"
        job_hashes = metadata_values(reply, "job_sha256")
        assert job_hashes and all(isinstance(item, str) and len(item) == 64
                                  and set(item) <= set("0123456789abcdef") for item in job_hashes)
        assert source.read_bytes() == raw
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                assert message.encode() not in path.read_bytes()
    finally:
        scene.close()
