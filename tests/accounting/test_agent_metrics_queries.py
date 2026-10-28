"""Strict API packets and extensible-but-strict-known operator configuration."""
from copy import deepcopy
import json

import pytest

from tests.accounting.agent_metrics_support import committed_observations, literal
from tests.accounting.test_agent_metrics_pipeline import open_scene, total_values
from tests.hook_transport.architecture_support import write


@pytest.mark.parametrize("case", literal("query-period-cases.json"), ids=lambda row: row["id"])
def test_source_time_filter_uses_aware_bounds_without_ingestion(project, tmp_path, case):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        scene.close()
        before = committed_observations(scene.metrics / "observed.sqlite")
        raw = (scene.sources / "session.jsonl").read_bytes()
        reply = scene.query(family="usage-current", period=case["period"])
        assert total_values(reply) == case["totals"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        assert (scene.sources / "session.jsonl").read_bytes() == raw
        assert not any(row.get("request", {}).get("operation") == "replay" for row in scene.calls)
    finally:
        scene.close()


@pytest.mark.parametrize("case", literal("query-source-cases.json"), ids=lambda row: row["id"])
def test_query_source_scope_keeps_the_producer_identity(project, tmp_path, case):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        scene.close()
        before = committed_observations(scene.metrics / "observed.sqlite")
        result = scene.query(family="usage-current", scope={"kind": "source", "id": case["id"]})
        assert total_values(result) == case["totals"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
    finally:
        scene.close()


def test_full_machine_query_is_not_clipped_by_the_agent_display_budget(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        complete = scene.wait_query(family="usage-current")
        scene.close()
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
        scene.close()


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
