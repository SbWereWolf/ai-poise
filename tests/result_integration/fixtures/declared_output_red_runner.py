"""Record exact missing-output-directory failures and preserve full diagnostics."""
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
            self.failed.append({
                "nodeid": report.nodeid,
                "phase": report.when,
                "missing_output_directory": "KeyError: 'POISE_RUN_OUTPUT_DIR'" in str(report.longrepr),
            })


recorder = Recorder()
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    code = pytest.main(["-q", "--tb=short", "tests/result_integration/test_declared_outputs.py"], plugins=[recorder])
Path(os.environ["POISE_RUN_OUTPUT_DIR"], "pytest.txt").write_text(stdout.getvalue() + stderr.getvalue())
print(json.dumps({"collected": recorder.collected, "passed": recorder.passed,
                  "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"]))},
                 sort_keys=True, separators=(",", ":")))
raise SystemExit(int(code))
