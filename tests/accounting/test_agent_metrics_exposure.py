"""A byte-based proxy stops at real compaction and never becomes provider spend."""
import json

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, assert_metadata, assert_unavailable_metadata,
    committed_observations, literal,
)
from tests.accounting.test_agent_metrics_pipeline import total_values


def test_explicit_exposure_proxy_counts_only_two_following_calls_before_compaction(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source("exposure")
    before = source.read_bytes()
    offset, output_offset, model_offsets = 0, None, []
    for line in before.splitlines(keepends=True):
        event = json.loads(line)
        if event["payload"].get("type") == "custom_tool_call_output":
            output_offset = offset
        if event["type"] == "token_usage_record":
            model_offsets.append(offset)
        offset += len(line)
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        proxy = scene.wait_query(family="exposure", kind="derived", predicate=lambda value: any(
            row["name"] == "gross_context_exposure" and row["value"] == 2 for row in value["metrics"]))
        assert_measurements(proxy, literal("expected-exposure.json"))
        assert_metadata(proxy, literal("expected-exposure-context.json"))
        assert_metadata(proxy, {"source_offset": output_offset, "first_seen_model_call": model_offsets[0],
                                "last_possible_model_call": model_offsets[1]})
        assert_unavailable_metadata(proxy, "path", state="not_applicable", source="codex-transcript")
        assert any(row["kind"] == "proxy" for row in proxy["metrics"])
        facts = scene.query()
        assert all(row["kind"] != "proxy" for row in facts["metrics"])
        usage = scene.query(family="usage-current")
        assert total_values(usage) == [10, 20, 150]
        scene.close()
        committed = committed_observations(scene.metrics / "observed.sqlite")
        again = scene.query(family="exposure", kind="derived")
        assert again["metrics"] == proxy["metrics"]
        assert committed_observations(scene.metrics / "observed.sqlite") == committed
        assert source.read_bytes() == before
        for path in scene.metrics.rglob("*"):
            if path.is_file():
                assert b"PRIVATE_F16_COMPACTION" not in path.read_bytes()
    finally:
        scene.close()
