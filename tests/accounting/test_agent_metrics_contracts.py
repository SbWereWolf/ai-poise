"""F16 public admission RED and permanent producer/query contracts.

The missing entry is reported as admission only. Later assertions remain present;
there is no xfail, skip, manufactured normalization, or successful fake receiver.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from tests.accounting.agent_metrics_support import (
    MetricsScene, assert_measurements, assert_metadata, literal, metadata_values,
)
from tests.hook_transport.architecture_support import write


def test_public_cli_reports_unavailable_taskless_store(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    result = scene.cli()
    assert result["reached"] == ["real-poise-module-entry"]
    if result["exit_code"] == 2 and "invalid choice: 'telemetry'" in result["stderr"]:
        raise AssertionError("F16_METRICS_CLI_ENTRY_MISSING: actual argparse rejected telemetry")
    assert result["exit_code"] == 2
    value = json.loads(result["stdout"])
    assert value["status"] == "unavailable"
    assert value["continuation"]
    assert result["sentinel"] == literal("expected-no-task.json")
    assert not scene.metrics.exists(), "F16_QUERY_CREATED_ABSENT_HISTORY"


def test_native_metrics_capability_is_a_registered_public_owner(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        result = scene.wait_query(family="usage-current")
        assert_measurements(result, literal("expected-usage-complete.json"))
    finally:
        scene.close()


@pytest.mark.parametrize("case", ["complete", "zero", "missing", "conflict"])
def test_real_source_usage_preserves_values_units_and_availability(project, tmp_path, case):
    scene = MetricsScene(project, tmp_path)
    source = scene.source(case)
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-usage-" + case + ".json")
        reply = scene.wait_measurements("usage-current", expected)
        assert source.read_bytes() == original
        assert len({row["pid"] for row in scene.calls if row["kind"] == "cli"}) >= 1
    finally:
        scene.close()


@pytest.mark.parametrize("location", ["root", "accounting", "collection", "delivery-policy", "analyses"])
def test_unknown_operator_options_are_ignored_without_rewriting_policy(project, tmp_path, location):
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        baseline = scene.wait_query(family="usage-current")
        extension = literal("extension-values.json")
        changed = deepcopy(scene.policy)
        target = {"root": changed, "accounting": changed["accounting"],
                  "collection": changed["collection"], "delivery-policy": changed["delivery"]["policy"],
                  "analyses": changed["analyses"]}[location]
        target.update(extension)
        write(scene.policy_path, changed)
        raw = scene.policy_path.read_bytes()
        after = scene.query(family="usage-current")
        assert_measurements(after, literal("expected-usage-complete.json"))
        assert after["metrics"] == baseline["metrics"]
        assert after["provenance"] == baseline["provenance"]
        assert "UNREGISTERED-F16" not in json.dumps(after)
        assert scene.policy_path.read_bytes() == raw
    finally:
        scene.close()


def test_existing_native_fixture_reaches_events_without_metrics(project, tmp_path):
    """Unchanged positive control: setup is genuine, not expected product RED."""
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    before = source.read_bytes()
    scene.install(metrics=False)
    try:
        scene.native("SessionStart")
        scene.native("UserPromptSubmit")
        scene.native("Stop")
        assert not scene.metrics.exists()
        assert source.read_bytes() == before
        assert len([row for row in scene.calls if row["kind"] == "native"]) == 3
    finally:
        scene.close()


@pytest.mark.parametrize("case", ["complete", "missing"])
def test_source_timing_is_not_worker_latency_or_an_invented_first_response(project, tmp_path, case):
    scene = MetricsScene(project, tmp_path)
    source = scene.source(case)
    before = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        expected = literal("expected-turn-timing-" + case + ".json")
        reply = scene.wait_measurements("turn-timing-context", expected)
        assert source.read_bytes() == before
    finally:
        scene.close()


@pytest.mark.parametrize("case", literal("usage-history-cases.json"), ids=lambda row: row["source"])
def test_usage_alternatives_reset_and_inherited_prefix_keep_truthful_current_totals(project, tmp_path, case):
    from tests.accounting.test_agent_metrics_pipeline import total_values

    scene = MetricsScene(project, tmp_path)
    source = scene.source(case["source"])
    before = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="usage-current", predicate=lambda value: total_values(value) == case["totals"])
        assert total_values(reply) == case["totals"]
        unknown = [row for row in reply["metrics"] if row["name"] == "total_tokens" and row["value"] is None]
        assert len(unknown) == case["unknown_totals"]
        assert all(row["availability"]["state"] == "unknown" and row["availability"]["reason"] for row in unknown)
        if case["excluded_prefix"]:
            assert "excluded_prefix_events" in json.dumps(reply["coverage"])
            assert reply["coverage"]["excluded_prefix_events"] > 0
        assert source.read_bytes() == before
        scene.native("Stop")
        assert scene.query(family="usage-current")["metrics"] == reply["metrics"]
    finally:
        scene.close()




def test_full_turn_context_keeps_capacity_model_effort_compaction_and_actual_source_times(project, tmp_path):
    from tests.accounting.test_agent_metrics_pipeline import total_values

    scene = MetricsScene(project, tmp_path)
    source = scene.source("rich-turn-context")
    original = source.read_bytes()
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_measurements("turn-timing-context", literal("expected-rich-turn-context.json"))
        assert_metadata(reply, literal("expected-rich-turn-metadata.json"))
        for field, expected in (("started_at", datetime(2026, 10, 29, tzinfo=timezone.utc)),
                                ("completed_at", datetime(2026, 10, 29, 0, 0, 2, tzinfo=timezone.utc))):
            assert any(datetime.fromisoformat(value.replace("Z", "+00:00")) == expected
                       for value in metadata_values(reply, field)), field
        assert total_values(scene.query(family="usage-current")) == [150]
        assert source.read_bytes() == original
    finally:
        scene.close()
