"""Stable exact RED observations; retain complete pytest diagnostics separately."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Observations:
    def __init__(self):
        self.collected = 0
        self.failed = []
        self.errors = []
        self.passed = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            target = self.failed if report.when == 'call' else self.errors
            target.append(report.nodeid.split('::', 1)[-1])
        elif report.when == 'call' and report.passed:
            self.passed += 1


observed = Observations()
output = io.StringIO()
with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
    code = pytest.main(['-q', '--tb=short', 'tests/requirements_registry/test_data_migrations.py'], plugins=[observed])
record = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'pytest.txt'
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(output.getvalue(), encoding='utf-8')
print(json.dumps({'collected': observed.collected, 'errors': sorted(observed.errors),
                  'failed': sorted(observed.failed), 'passed': observed.passed},
                 ensure_ascii=False, sort_keys=True, separators=(',', ':')))
sys.exit(int(code))
