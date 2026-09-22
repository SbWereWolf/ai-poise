"""Shared subprocess transport for the two DDD CLI boundary scenarios."""
import json
import os
from pathlib import Path
import subprocess
import sys


INHERITED_IDENTITIES = (
    {},
    {"POISE_SESSION": "foreign-session", "POISE_CALLER_BINDING": "/missing/foreign.json"},
    {"CODEX_SESSION_ID": "foreign-session", "CODEX_THREAD_ID": "foreign-thread",
     "POISE_CALLER_BINDING": "/missing/foreign.json", "POISE_CONFIG": "/missing/project.json"},
)


def cli_environment(project, binding):
    """Use a new fixture-owned binding, never an inherited actor or launcher."""
    return {
        **{key: value for key, value in os.environ.items()
           if not key.startswith(("POISE_", "CODEX_"))},
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "POISE_CONFIG": str(project["config_path"]),
        "POISE_CALLER_BINDING": str(binding),
    }


def run_work(environment, packet):
    """Return the exit code and complete response, not its bounded preview."""
    process = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "work"],
        input=json.dumps(packet), env=environment,
        capture_output=True, text=True, timeout=15,
    )
    assert process.stdout.strip(), process.stderr
    value = json.loads(process.stdout)
    if value.get("response_path"):
        value = json.loads(Path(value["response_path"]).read_text(encoding="utf-8"))
    return process, value


def bootstrap_packet(task=None):
    return {"operation": "bootstrap", "input": {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }, "messages": []}
