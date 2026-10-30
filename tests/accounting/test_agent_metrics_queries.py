"""Strict API packets and extensible-but-strict-known operator configuration."""
from copy import deepcopy
import json

import pytest

from tests.accounting.agent_metrics_support import (
    MetricsScene, ProcessorGate, committed_observations, literal,
)
from tests.accounting.test_agent_metrics_pipeline import open_scene, total_values
from tests.hook_transport.architecture_support import write


def query_scene(project, tmp_path):
    """Prepare without launching an unobserved initial recording producer."""
    scene = MetricsScene(project, tmp_path)
    scene.source()
    scene.install()
    return scene


def terminal_capture(scene, event):
    """Observe the one expected batch from this launch, then its actual termination."""
    gate = ProcessorGate(scene.parent / ("query-capture-" + event))
    trace = gate.root / "capture-trace"
    try:
        scene.native(event, gate=gate.configuration, trace_directory=trace)
        # A missing/delayed producer must reach its gate; an empty PID set is
        # never interpreted as completion. Each fixture event submits one batch.
        entered = gate.wait_entered()
        assert len(entered) == 1, ("F16_EXPECTED_ONE_CAPTURE_PRODUCER", event, entered)
        gate.release()
        gate.wait_terminal()
        observed = [json.loads(line) for path in trace.glob("*.jsonl")
                    for line in path.read_text().splitlines()]
        captures = [row for row in observed if row["boundary"] == "actual-capture-return"]
        producers = [row for row in observed if row["boundary"] == "actual-detached-entry"]
        assert len(captures) == 1 and captures[0]["jobs"] > 0, (
            "F16_EXPECTED_CAPTURE_NOT_OBSERVED", event, observed)
        assert len(producers) == 1 and producers[0]["pid"] == entered[0]["pid"], (
            "F16_EXPECTED_PRODUCER_NOT_OBSERVED", event, observed, entered)
        assert producers[0]["pid"] != captures[0]["pid"]
        scene.record({"kind": "query-capture-producer-terminal", "event": event,
                      "expected_producers": 1, "entered": entered,
                      "capture_observations": observed, "terminal": True})
    finally:
        gate.release()
        gate.wait_terminal()


@pytest.mark.parametrize("case", literal("query-period-cases.json"), ids=lambda row: row["id"])
def test_source_time_filter_uses_aware_bounds_without_ingestion(project, tmp_path, case):
    scene = query_scene(project, tmp_path)
    try:
        terminal_capture(scene, "SessionStart")
        terminal_capture(scene, "Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        terminal_capture(scene, "SessionEnd")
        before = committed_observations(scene.metrics / "observed.sqlite")
        raw = (scene.sources / "session.jsonl").read_bytes()
        reply = scene.query(family="usage-current", period=case["period"])
        assert total_values(reply) == case["totals"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        assert (scene.sources / "session.jsonl").read_bytes() == raw
        assert not any(row.get("request", {}).get("operation") == "replay" for row in scene.calls)
    finally:
        if scene.started:
            terminal_capture(scene, "SessionEnd")


@pytest.mark.parametrize("case", literal("query-source-cases.json"), ids=lambda row: row["id"])
def test_query_source_scope_keeps_the_producer_identity(project, tmp_path, case):
    scene = query_scene(project, tmp_path)
    try:
        terminal_capture(scene, "SessionStart")
        terminal_capture(scene, "Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        terminal_capture(scene, "SessionEnd")
        before = committed_observations(scene.metrics / "observed.sqlite")
        result = scene.query(family="usage-current", scope={"kind": "source", "id": case["id"]})
        assert total_values(result) == case["totals"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        if scene.started:
            terminal_capture(scene, "SessionEnd")


def test_full_machine_query_is_not_clipped_by_the_agent_display_budget(project, tmp_path):
    scene = query_scene(project, tmp_path)
    try:
        terminal_capture(scene, "SessionStart")
        terminal_capture(scene, "Stop")
        complete = scene.wait_query(family="usage-current")
        terminal_capture(scene, "SessionEnd")
        scene.general.profile["limits"]["output_chars"] = 512
        scene.general.publish(request_id="f16-small-agent-display")
        before = committed_observations(scene.metrics / "observed.sqlite")
        full = scene.query(family="usage-current")
        actual = [row for row in scene.calls if row["kind"] == "cli"][-1]
        assert len(actual["stdout"]) > 512
        assert full["metrics"] == complete["metrics"]
        assert full["provenance"] == complete["provenance"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        if scene.started:
            terminal_capture(scene, "SessionEnd")


@pytest.mark.parametrize("case", literal("api-invalid-cases.json"), ids=lambda row: row["id"])
def test_api_packet_rejections_are_real_and_preserve_previous_metrics(project, tmp_path, case):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        before = scene.wait_query(family="usage-current")
        request = literal("query.json")
        request.update(deepcopy(case["change"]))
        result = scene.cli(request)
        assert result["reached"] == ["real-poise-module-entry"]
        assert result["exit_code"] == 2
        reply = json.loads(result["stdout"])
        assert reply["status"] == "rejected"
        assert reply["reason"] and reply["continuation"]
        assert scene.query(family="usage-current")["metrics"] == before["metrics"]
    finally:
        scene.close()


@pytest.mark.parametrize("case", literal("known-policy-invalid-cases.json"), ids=lambda row: row["id"])
def test_known_policy_settings_remain_strict_without_rewriting_raw_bytes(project, tmp_path, case):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        before = scene.wait_query(family="usage-current")
        altered = deepcopy(scene.policy)
        target = altered
        for key in case["path"][:-1]:
            target = target[key]
        target[case["path"][-1]] = case["value"]
        write(scene.policy_path, altered)
        raw = scene.policy_path.read_bytes()
        result = scene.cli()
        assert result["exit_code"] == 2
        rejected = json.loads(result["stdout"])
        assert rejected["status"] == "rejected"
        assert rejected["reason"] and rejected["continuation"]
        assert scene.policy_path.read_bytes() == raw
        write(scene.policy_path, scene.policy)
        assert scene.query(family="usage-current")["metrics"] == before["metrics"]
    finally:
        # Restore only this test's raw policy so genuine End remains independently valid.
        write(scene.policy_path, scene.policy)
        scene.close()
