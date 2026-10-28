"""Exact source identities and byte-bound provenance, read from another real process."""
import hashlib
import json
from datetime import datetime, timezone

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_metadata, assert_unavailable_metadata, literal, metadata_values,
)


def test_complete_source_provenance_matches_actual_capture_bytes_and_response_identity(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    raw = source.read_bytes()
    usage_line, = [line for line in raw.splitlines() if json.loads(line)["type"] == "token_usage_record"]
    scene.install()
    before = datetime.now(timezone.utc)
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="invocation-provenance", predicate=lambda value:
                                  "response-f16-a" in metadata_values(value, "response_id"))
        assert_metadata(reply, literal("expected-invocation-metadata.json"))
        assert_metadata(reply, {"boundary": len(raw), "complete_offset": len(raw),
                                "byte_offset": raw.index(usage_line)})
        actual_hashes = metadata_values(reply, "source_content_hash") + metadata_values(reply, "sha256")
        assert hashlib.sha256(raw).hexdigest() in actual_hashes
        event_hashes = metadata_values(reply, "event_hash") + metadata_values(reply, "event_sha256")
        assert hashlib.sha256(usage_line).hexdigest() in event_hashes
        identities = set(metadata_values(reply, "invocation_key"))
        assert len(identities) == 1 and all(isinstance(item, str) and item for item in identities)
        for field in ("source_key", "classification_evidence_hash"):
            values = metadata_values(reply, field)
            assert values and all(isinstance(item, str) and item for item in values), field
        evidence_hashes = metadata_values(reply, "classification_evidence_hash")
        assert all(len(item) == 64 and set(item) <= set("0123456789abcdef") for item in evidence_hashes)
        # No response-linked tool exists in this complete source. The donor's
        # versioned evidence digest is SHA-256 of canonical [], not any hex value.
        assert set(evidence_hashes) == {"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}
        times = metadata_values(reply, "timestamp")
        assert any(datetime.fromisoformat(item.replace("Z", "+00:00")) ==
                   datetime(2026, 10, 29, 0, 0, 1, tzinfo=timezone.utc) for item in times)
        processed = metadata_values(reply, "processed_at")
        after = datetime.now(timezone.utc)
        assert processed
        assert all(before <= datetime.fromisoformat(item.replace("Z", "+00:00")) <= after for item in processed)
        assert_unavailable_metadata(reply, "model", state="unknown", source="codex-transcript")
        again = scene.query(family="invocation-provenance")
        assert set(metadata_values(again, "invocation_key")) == identities
        assert_metadata(again, literal("expected-invocation-metadata.json"))
        assert source.read_bytes() == raw
    finally:
        scene.close()
