"""Actual processing barriers: a temporary pending state never proves success."""
import json
import os
import subprocess
import sys

from tests.accounting.agent_metrics_support import (
    FIXTURES, MetricsScene, ProcessorGate, SESSION, assert_measurements, literal,
)


def test_processor_observer_holds_and_releases_the_real_existing_boundary(tmp_path):
    gate = ProcessorGate(tmp_path)
    effect = tmp_path / "actual-effect.txt"
    request = literal("gate-control-request.json")
    request.update(driver=str(FIXTURES / "cli_driver.py"), gate=gate.configuration, effect=str(effect))
    environment = {**os.environ, "PYTHONPATH": str(FIXTURES.parents[3] / "src")}
    process = subprocess.Popen([sys.executable, "-B", str(FIXTURES / "gate_control.py")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment)
    process.stdin.write(json.dumps(request).encode())
    process.stdin.close()
    process.stdin = None
    try:
        entered = gate.wait_entered()
        assert entered[0]["pid"] == process.pid
        assert not effect.exists(), "F16_OBSERVER_DID_NOT_HOLD_REAL_EFFECT"
        gate.release()
        stdout, stderr = process.communicate()
        assert process.returncode == 0 and stdout == stderr == b"", (stdout, stderr)
        assert effect.read_text() == "received\n"
        gate.wait_terminal()
    finally:
        gate.release()
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_end_before_processing_still_finishes_commit_and_consumed_input_cleanup(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    source = scene.source()
    original = source.read_bytes()
    scene.install()
    gate = ProcessorGate(tmp_path)
    try:
        # The barrier is before the actual shared processor, not after source consumption.
        scene.native("SessionStart", gate=gate.configuration)
        entered = gate.wait_entered()
        assert entered
        scene.native("Stop", gate=gate.configuration)
        scene.native("SessionEnd", gate=gate.configuration)
        # Releasing alone is insufficient: require actual stored data and terminal consumers.
        gate.release()
        reply = scene.wait_query(family="usage-current")
        assert_measurements(reply, literal("expected-usage-complete.json"))
        gate.wait_terminal()
        deliveries = []
        for path in scene.service.settings.observations.rglob("response.json"):
            data = json.loads(path.read_text())
            if (data.get("schema") == "poise-hook-event-delivery-2"
                    and data["event_receipt"]["session_id"] == SESSION):
                deliveries.append(data)
                assert data["chain_complete"] and not data["chain_failed"]
                assert data["inputs_retired"] is True
                for invocation in data["invocations"]:
                    assert not (path.parent / invocation["invocation_id"] / "input.json").exists()
        assert len(deliveries) >= 3, "F16_LIFETIME_ACTUAL_DELIVERIES_NOT_OBSERVED"
        assert source.read_bytes() == original
        assert (scene.metrics / "observed.sqlite").is_file()
    finally:
        gate.release()
        scene.close()
        if gate.root.exists() and list(gate.root.glob("*.entered.json")):
            gate.wait_terminal()
