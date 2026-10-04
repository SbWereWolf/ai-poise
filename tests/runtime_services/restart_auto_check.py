"""Record exact behavioral RED causes and full diagnostics for replay tests."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest


SELECTION = [
    "tests/tasks/test_progression_modes.py",
    "tests/runtime_services/test_restart_auto_replay.py",
    "tests/runtime_services/test_restart_auto_safety.py",
]


class Recorder:
    def __init__(self):
        self.collected = 0
        self.passed = 0
        self.failed = []

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
        if not report.failed:
            return
        if "test_default_mode_replays_accepted_stages" in report.nodeid:
            marker = "advance input: required fields ['request_id', 'target_stage', 'task_id']"
        elif ("test_uncommitted_task_work" in report.nodeid
              or "test_post_checkout_interruption" in report.nodeid):
            marker = "DID NOT RAISE"
        else:
            marker = "Public replay assessment is missing"
        self.failed.append({
            "nodeid": report.nodeid, "phase": report.when,
            "intentional": report.when == "call" and marker in str(report.longrepr),
        })


def main():
    if sys.argv[1:] not in (["red"], ["green"]):
        raise ValueError("Explicit red or green mode required")
    recorder = Recorder()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = pytest.main(["-q", "--tb=short", *SELECTION], plugins=[recorder])
    destination = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
    (destination / "pytest.txt").write_text(output.getvalue())
    summary = {
        "collected": recorder.collected, "passed": recorder.passed,
        "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"])),
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
