"""Actual worker death after committed receiver effects, before spool acknowledgment."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from tests.accounting.agent_metrics_support import (
    FIXTURES, MetricsScene, committed_observations, literal, replace_tokens,
)
from tests.accounting.test_agent_metrics_pipeline import total_values


def test_real_commit_exit_observer_keeps_unacknowledged_queue_record(tmp_path):
    configuration = replace_tokens(literal("commit-exit.json"), {"@COMMIT_EXIT@": tmp_path / "witness"})
    request = literal("commit-control-request.json")
    request.update(driver=str(FIXTURES / "cli_driver.py"), configuration=configuration,
                   spool=str(tmp_path / "spool"), policy=literal("policy.json")["delivery"]["policy"],
                   effect=str(tmp_path / "actual-committed-effect.txt"))
    result = subprocess.run([sys.executable, "-B", str(FIXTURES / "commit_control.py")],
                            input=json.dumps(request).encode(), capture_output=True,
                            env={**os.environ, "PYTHONPATH": str(FIXTURES.parents[3] / "src")})
    assert result.returncode == 0 and not result.stderr, (result.stdout, result.stderr)
    actual = json.loads(result.stdout)
    (tmp_path / "observer-control-result.json").write_bytes(result.stdout)
    assert actual["actual_exit_code"] == 73
    assert actual["effect_before_replay"] == "f16-commit-observer-control\n"
    assert actual["pending_before_replay"]["pending"] == 1
    assert actual["replay"] == {"attempted": 1, "delivered": 1, "failed": 0, "corrupt": 0}
    assert actual["after"]["pending"] == 0
    witnesses = [json.loads(path.read_text()) for path in (tmp_path / "witness").glob("*.committed-before-ack.json")]
    assert len(witnesses) == 1 and witnesses[0]["pid"] == actual["child_pid"]


def test_committed_observation_survives_real_worker_death_before_ack_and_replay(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    scene.install()
    witness_root = tmp_path / "commit-before-ack"
    configuration = replace_tokens(literal("commit-exit.json"), {"@COMMIT_EXIT@": witness_root})
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        assert total_values(scene.wait_query(family="usage-current")) == [150]
        scene.wait_status(lambda result, value: result == 0 and value["delivery"]["pending"] == 0)
        baseline_ids = {row[0] for row in committed_observations(scene.metrics / "observed.sqlite")}
        with source.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(literal("additional-usage-event.json")) + "\n")
        original = source.read_bytes()
        scene.native("Stop", commit_exit=configuration)
        deadline = time.monotonic() + 15
        witnesses = []
        while time.monotonic() < deadline:
            witnesses = [json.loads(path.read_text()) for path in witness_root.glob("*.committed-before-ack.json")]
            if witnesses:
                break
            time.sleep(.02)
        assert witnesses, "F16_REAL_RECEIVER_RETURN_BEFORE_ACK_NOT_REACHED"
        before = committed_observations(scene.metrics / "observed.sqlite")
        identities = {row[0] for row in before}
        assert all(item["identity"] in identities for item in witnesses), "F16_CRASH_WITNESS_HAS_NO_REAL_COMMIT"
        assert any(item["identity"] not in baseline_ids for item in witnesses)
        assert all(item["pid"] != item["owner_pid"] and item["requested_exit_code"] == 73 for item in witnesses)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            live = []
            for item in witnesses:
                try:
                    process = psutil.Process(item["pid"])
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        live.append(item["pid"])
                except psutil.NoSuchProcess:
                    pass
            if not live:
                break
            time.sleep(.01)
        assert not live, ("F16_INJECTED_WORKER_NOT_TERMINAL", live)
        scene.record({"kind": "actual-commit-before-ack-workers-terminal", "witnesses": witnesses,
                      "terminal": True, "os_exit_status_available": False})
        assert all(Path(item["queued_path"]).is_file() for item in witnesses)
        # This read is before recovery; already committed effects must be visible.
        first = scene.query(family="usage-current")
        assert total_values(first) in ([150], [20, 150])
        time.sleep(scene.policy["delivery"]["policy"]["retry_seconds"])
        code, replay = scene.operation("replay")
        assert code == 0 and replay["delivered"] >= 1
        assert replay["failed"] == replay["corrupt"] == 0
        again = scene.wait_query(family="usage-current", predicate=lambda value: total_values(value) == [20, 150])
        assert total_values(again) == [20, 150]
        repeated = scene.query(family="usage-current")
        assert repeated["metrics"] == again["metrics"]
        after = committed_observations(scene.metrics / "observed.sqlite")
        assert set(before).issubset(set(after))
        assert all(sum(row[0] == identity for row in after) == 1 for identity in identities)
        scene.wait_status(lambda result, value: result == 0 and value["delivery"]["pending"] == 0)
        assert source.read_bytes() == original
    finally:
        scene.close()
