"""One standard pytest run; exact intended failures plus unabridged diagnostics."""
import contextlib
import io
import json
import os
from pathlib import Path

import pytest


MODULE = "tests/hook_transport/test_independent_operations.py"
SELECTION = [
    MODULE + "::test_native_event_finishes_before_preparation[UserPromptSubmit-install]",
    MODULE + "::test_native_event_finishes_before_preparation[UserPromptSubmit-reconcile]",
    MODULE + "::test_native_event_finishes_before_preparation[Stop-install]",
    MODULE + "::test_native_event_finishes_before_preparation[Stop-reconcile]",
    MODULE + "::test_native_event_finishes_before_preparation[SessionEnd-install]",
    MODULE + "::test_native_event_finishes_before_preparation[SessionEnd-reconcile]",
    MODULE + "::test_native_event_finishes_before_preparation[SessionStart-install]",
    MODULE + "::test_native_event_finishes_before_preparation[SessionStart-reconcile]",
    MODULE + "::test_competing_publication_is_preserved[install]",
    MODULE + "::test_competing_publication_is_preserved[reconcile]",
    MODULE + "::test_pending_reconfiguration_handles_session_end",
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
        if report.failed:
            crash = getattr(report.longrepr, "reprcrash", None)
            message = getattr(crash, "message", str(report.longrepr))
            cause = "unexpected"
            for marker in (
                "HOOK-PREP-WRITER-BLOCKS-EVENT",
                "HOOK-PREP-WRITER-BLOCKS-COMPETITOR",
                "HOOK-PENDING-END-CANNOT-RESUME",
            ):
                if message == "Failed: " + marker or message == marker:
                    cause = marker
                    break
            self.failed.append({"nodeid": report.nodeid, "phase": report.when, "cause": cause})


recorder = Recorder()
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    code = pytest.main(["-q", "--tb=short", "-pno:cacheprovider", *SELECTION], plugins=[recorder])
output = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
output.joinpath("pytest.stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
output.joinpath("pytest.stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
output.joinpath("pytest.txt").write_text(stdout.getvalue() + stderr.getvalue(), encoding="utf-8")
print(json.dumps({"collected": recorder.collected, "passed": recorder.passed,
                  "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"]))},
                 sort_keys=True, separators=(",", ":")))
raise SystemExit(int(code))
