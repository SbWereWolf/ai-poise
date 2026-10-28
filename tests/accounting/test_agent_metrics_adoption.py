"""Actual configured adoption-source ingestion, typed facts and retained provenance."""
import hashlib
import json

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, assert_metadata, literal,
)


def test_configured_adoption_source_preserves_observed_fields_without_raw_command_archive(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    event = literal("adoption-event.json")
    source = scene.sources / "adoption.jsonl"
    record = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
    source.write_bytes(record + b"\n")
    scene.add_source(literal("adoption-source.json"))
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="hook-adoption")
        assert_measurements(reply, literal("expected-adoption-measurements.json"))
        assert_metadata(reply, literal("expected-adoption-metadata.json"))
        # Direct byte/hash provenance; not an expected value from a product parser.
        assert_metadata(reply, {"event_sha256": hashlib.sha256(record).hexdigest()})
        payload = [row for row in reply["metrics"] if row["name"] == "payload_bytes"]
        assert len(payload) == 1 and payload[0]["value"] == len(record)
        assert payload[0]["unit"] == "bytes" and payload[0]["kind"] == "observed"
        assert source.read_bytes() == record + b"\n"
        scene.native("Stop")
        assert scene.query(family="hook-adoption")["metrics"] == reply["metrics"]
    finally:
        scene.close()
