"""Standard pytest execution with an exact cause summary and complete diagnostics."""
import contextlib
import io
import json
import os
from pathlib import Path

import pytest

NODE = "tests/terminal_materials/test_delivery.py::test_active_or_unagreed_terminal_task_cannot_be_retired"


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
        if report.failed:
            crash = getattr(report.longrepr, "reprcrash", None)
            message = getattr(crash, "message", "")
            self.failed.append({
                "nodeid": report.nodeid,
                "phase": report.when,
                "cause": message.startswith("Failed: DELIVERY-AGREEMENT-MISSING:"),
            })


recorder = Recorder()
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    code = pytest.main(["-q", "--tb=short", NODE], plugins=[recorder])
Path(os.environ["POISE_RUN_OUTPUT_DIR"], "pytest.txt").write_text(
    stdout.getvalue() + stderr.getvalue(), encoding="utf-8",
)
print(json.dumps({"collected": recorder.collected, "passed": recorder.passed,
                  "failed": recorder.failed}, sort_keys=True, separators=(",", ":")))
raise SystemExit(int(code))
