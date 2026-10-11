"""Existing pytest evidence projection with strict, test-owned collection coverage."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path

import pytest

from .preflight_pytest import Reports


FIXTURES = Path(__file__).parent / 'fixtures/material_relocation'
FILES = [
    'tests/projects/test_state_material_relocation.py',
    'tests/verification/test_material_relocation_consumers.py',
]


class CompleteReports(Reports):
    def __init__(self, expected):
        super().__init__()
        self.expected = sorted(expected)

    def pytest_collection_finish(self, session):
        super().pytest_collection_finish(session)
        if self.collected != self.expected or not self.collected:
            self.errors.append('collection:expected-inventory-mismatch')

    def pytest_runtest_logreport(self, report):
        if report.skipped or hasattr(report, 'wasxfail'):
            self.errors.append('outcome:' + report.nodeid + ':skip-or-xfail')
            if report.nodeid in self.passed:
                self.passed.remove(report.nodeid)


def run(selectors, expected, output):
    output.mkdir(parents=True, exist_ok=True)
    reports = CompleteReports(expected)
    with (output / 'pytest.stdout.txt').open('w', encoding='utf-8') as stdout, \
            (output / 'pytest.stderr.txt').open('w', encoding='utf-8') as stderr:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = int(pytest.main(['-q', '-rA', *selectors], plugins=[reports]))
    summary = {
        'collected': reports.collected,
        'errors': sorted(set(reports.errors)),
        'exit_code': code,
        'failed': sorted(reports.failed),
        'passed': sorted(reports.passed),
    }
    # Preserve pytest's actual exit in the summary; the wrapper additionally refuses
    # an incomplete inventory or a skipped obligation even when pytest exits zero.
    return code if code else (2 if reports.errors else 0), summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', required=True, choices=['red', 'all'])
    args = parser.parse_args()
    expected = json.loads((FIXTURES / (args.selection + '-nodes.json')).read_text())
    selectors = expected if args.selection == 'red' else FILES
    code, summary = run(selectors, expected, Path(os.environ['POISE_RUN_OUTPUT_DIR']))
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
