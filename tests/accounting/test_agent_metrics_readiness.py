"""Documentation-only readiness labels are not fabricated available measurements."""
import json

from tests.accounting.agent_metrics_support import committed_observations, literal
from tests.accounting.test_agent_metrics_pipeline import open_scene, total_values


def test_documented_only_readiness_does_not_become_a_successful_empty_score(project, tmp_path):
    scene = open_scene(project, tmp_path)
    try:
        scene.native("Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        scene.close()
        before = committed_observations(scene.metrics / "observed.sqlite")
        policy = scene.policy_path.read_bytes()
        request = literal("query.json")
        request["view"].update(kind="derived", family="readiness-documented-only")
        actual = scene.cli(request)
        assert actual["reached"] == ["real-poise-module-entry"]
        if actual["exit_code"] == 2 and "invalid choice: 'telemetry'" in actual["stderr"]:
            raise AssertionError("F16_METRICS_CLI_ENTRY_MISSING: actual argparse rejected telemetry")
        result = json.loads(actual["stdout"])
        if actual["exit_code"] == 2:
            assert result["status"] in {"unavailable", "rejected"}
            assert result["reason"] and result["continuation"]
        else:
            assert actual["exit_code"] == 0 and result["status"] == "read_only"
            assert result["metrics"] == []
            assert result["coverage"]["state"] == "unavailable"
            assert result["coverage"]["reason"]
        assert committed_observations(scene.metrics / "observed.sqlite") == before
        assert scene.policy_path.read_bytes() == policy
        assert total_values(scene.query(family="usage-current")) == [150]
    finally:
        scene.close()
