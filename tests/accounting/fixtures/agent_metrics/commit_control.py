"""Exercise the failure observer through the real existing spool in an owned child."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time


request = json.load(sys.stdin)
specification = importlib.util.spec_from_file_location("f16_commit_observer", request["driver"])
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module.observe_commit_exit(request["configuration"])
from poise.application.telemetry import TelemetryEnvelope
from poise.infrastructure.telemetry_spool import TelemetrySpool


def actual_receiver(envelope):
    Path(request["effect"]).write_text(envelope.identity + "\n")


child = os.fork()
if child == 0:
    service = TelemetrySpool(Path(request["spool"]), request["policy"], actual_receiver)
    service.enqueue(TelemetryEnvelope("f16-commit-observer-control", {"fixture": 17}))
    service.replay()
    os._exit(90)  # A missing observer must be observable as the wrong exit.

deadline = time.monotonic() + 10
while True:
    terminated, status = os.waitpid(child, os.WNOHANG)
    if terminated:
        break
    if time.monotonic() >= deadline:
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
        raise AssertionError("F16_OBSERVER_CONTROL_CHILD_NOT_TERMINAL")
    time.sleep(.01)
service = TelemetrySpool(Path(request["spool"]), request["policy"], actual_receiver)
pending = service.status()
effect = Path(request["effect"]).read_text()
time.sleep(request["policy"]["retry_seconds"])
replay = service.replay()
print(json.dumps({"child_pid": child, "actual_exit_code": os.waitstatus_to_exitcode(status),
                  "effect_before_replay": effect, "pending_before_replay": pending,
                  "replay": replay, "after": service.status()}))
