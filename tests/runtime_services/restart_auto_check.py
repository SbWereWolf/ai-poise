"""Record exact behavioral RED causes and full diagnostics for replay tests."""

import contextlib
import json
import os
from pathlib import Path
import sys

import pytest


SELECTION = [
    "tests/tasks/test_progression_modes.py",
    "tests/runtime_services/test_restart_auto_replay.py",
    "tests/runtime_services/test_restart_auto_safety.py",
    "tests/runtime_services/test_restart_auto_history.py",
    "tests/runtime_services/test_restart_auto_durability.py",
    "tests/runtime_services/test_restart_auto_noop_identity.py",
    "tests/runtime_services/test_restart_auto_noop_concurrency.py",
]

GROUPS = {
    "modes": [SELECTION[0]],
    "proof-files": [SELECTION[1], "-k", "unchanged_accepted_file or required_file_failure or missing_recorded_commit"],
    "proof-checks": [SELECTION[1], "-k", "not (unchanged_accepted_file or required_file_failure or missing_recorded_commit)"],
    "safety": [SELECTION[2]],
    "history": [SELECTION[3], "-k", "not (changed_route or maximum_replay)"],
    "history-route": [SELECTION[3], "-k", "changed_route or maximum_replay"],
    "durability-boundaries": [SELECTION[4], "-k", "each_confirmed_boundary or unsafe_material"],
    "durability-effects": [SELECTION[4], "-k", "not (each_confirmed_boundary or unsafe_material)"],
    "noop-repeat": [SELECTION[5], "-k", "not (changed_intent or current_admission)"],
    "noop-conflicts": [SELECTION[5], "-k", "changed_intent"],
    "noop-admission": [SELECTION[5], "-k", "current_admission"],
    "noop-concurrency": [SELECTION[6]],
}


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
        if ("test_default_mode_replays_accepted_stages" in report.nodeid
                or "test_maximum_replay_respects_separate_final_authority" in report.nodeid):
            marker = "advance input: required fields ['request_id', 'target_stage', 'task_id']"
        elif ("test_uncommitted_task_work" in report.nodeid
              or "test_post_checkout_interruption" in report.nodeid):
            marker = "DID NOT RAISE"
        elif any(name in report.nodeid for name in (
                "test_each_confirmed_boundary", "test_launched_check_with_unknown",
                "test_unsafe_material_refuses")):
            marker = "DID NOT RAISE"
        else:
            marker = "Public replay assessment is missing"
        self.failed.append({
            "nodeid": report.nodeid, "phase": report.when,
            "intentional": report.when == "call" and marker in str(report.longrepr),
        })


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("red", "green") or sys.argv[2] not in GROUPS:
        raise ValueError("Explicit red/green mode and bounded group required")
    recorder = Recorder()
    destination = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
    with (destination / "pytest.txt").open("w", buffering=1) as output:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = pytest.main(["-q", "--tb=short", "--durations=5", *GROUPS[sys.argv[2]]], plugins=[recorder])
    summary = {
        "collected": recorder.collected, "passed": recorder.passed,
        "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"])),
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
