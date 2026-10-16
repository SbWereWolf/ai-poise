"""One standard pytest invocation, exact failure identities, complete diagnostics."""
import contextlib
import io
import json
import os
from pathlib import Path

import pytest


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
            text = str(report.longrepr)
            kind = "unexpected_failure"
            if "AssertionError" in text:
                for marker, label in (
                    ("startup-summary-missing", "missing_session_summary"),
                    ("saved-read-summary-missing", "missing_saved_read"),
                    ("refresh-summary-missing", "missing_refresh"),
                ):
                    if marker in text:
                        kind = label
                        break
            self.failed.append({"nodeid": report.nodeid, "phase": report.when, "kind": kind})


recorder = Recorder()
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    code = pytest.main(["-q", "--tb=short", "tests/hook_transport/test_session_readiness.py"],
                       plugins=[recorder])
output = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
output.joinpath("pytest.stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
output.joinpath("pytest.stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
output.joinpath("pytest.txt").write_text(stdout.getvalue() + stderr.getvalue(), encoding="utf-8")
print(json.dumps({"collected": recorder.collected, "passed": recorder.passed,
                  "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"]))},
                 sort_keys=True, indent=2))
raise SystemExit(int(code))
