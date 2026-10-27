"""One pytest invocation with exact failure identities and complete raw streams."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest


class Recorder:
    def __init__(self):
        self.collected = 0
        self.passed = 0
        self.failed = []

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.failed.append({"nodeid": report.nodeid, "phase": "collection", "kind": "unexpected"})

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
        if report.failed:
            text = str(report.longrepr)
            kind = "unexpected"
            if "AssertionError" in text:
                for marker in (
                    "SCHEDULE_FIELD_DIAGNOSTIC_MISSING",
                    "SCHEDULE_REMEDY_DIAGNOSTIC_MISSING",
                    "READY_DOMAIN_DIAGNOSTIC_MISSING",
                    "READY_FIELD_DIAGNOSTIC_MISSING",
                    "READY_REMEDY_DIAGNOSTIC_MISSING",
                ):
                    if marker in text:
                        kind = marker
                        break
            self.failed.append({"nodeid": report.nodeid, "phase": report.when, "kind": kind})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("diagnostics", "preservation", "regression"), required=True)
    args = parser.parse_args()
    if args.group == "regression":
        selected = ["tests/verification/test_plan_contract.py"]
    else:
        selected = ["tests/verification/test_ready_schedule_diagnostics.py",
                    "tests/tasks/test_ready_schedule_boundary.py", "-k", "test_" + args.group]
    recorder = Recorder()
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", *selected],
                           plugins=[recorder])
    output = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
    output.joinpath("pytest.stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    output.joinpath("pytest.stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
    print(json.dumps({"collected": recorder.collected, "passed": recorder.passed,
                      "failed": sorted(recorder.failed, key=lambda item: (item["nodeid"], item["phase"]))},
                     sort_keys=True, separators=(",", ":")))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
