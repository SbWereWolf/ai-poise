"""Exercise the observer against the unchanged real shared processor."""
import importlib.util
import json
from pathlib import Path
import sys


request = json.load(sys.stdin)
specification = importlib.util.spec_from_file_location("f16_cli_observer", request["driver"])
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module.observe_gate(request["gate"])
from poise.infrastructure.telemetry import DetachedTelemetryProcessor


def real_effect(value):
    Path(request["effect"]).write_text(value)


DetachedTelemetryProcessor(real_effect)("received\n")
