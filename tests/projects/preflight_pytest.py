"""Stable evidence projection of standard pytest; preserve its complete output.

No failure is reclassified as success. Assertion failures, setup/teardown errors,
collection errors and pytest's terminal exit code have distinct literal fields.
The declared run-output owner supplies the directory for the complete diagnostics.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path

import pytest


class Reports:
    def __init__(self):
        self.collected = []
        self.failed = []
        self.errors = []
        self.passed = []

    def pytest_collection_finish(self, session):
        self.collected = sorted(item.nodeid for item in session.items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(f'collection:{report.nodeid}')

    def pytest_runtest_makereport(self, item, call):
        if call.excinfo is not None:
            if call.when == 'call' and call.excinfo.errisinstance(AssertionError):
                self.failed.append(item.nodeid)
            else:
                self.errors.append(f'{call.when}:{item.nodeid}:{call.excinfo.typename}')
        elif call.when == 'call':
            self.passed.append(item.nodeid)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', required=True, choices=['behavior', 'all'])
    args = parser.parse_args()
    output = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    reports = Reports()
    stdout, stderr = io.StringIO(), io.StringIO()
    selectors = ['tests/projects/test_project_preflight.py',
                 'tests/projects/test_preflight_store_preservation.py']
    if args.selection == 'behavior':
        selectors += ['-k', 'not advertised_git_update and not unavailable_settings and not missing_settings_argument']
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = int(pytest.main(['-q', *selectors], plugins=[reports]))
    (output / 'pytest.stdout.txt').write_text(stdout.getvalue(), encoding='utf-8')
    (output / 'pytest.stderr.txt').write_text(stderr.getvalue(), encoding='utf-8')
    summary = {'collected': reports.collected, 'errors': sorted(reports.errors),
               'exit_code': code, 'failed': sorted(reports.failed), 'passed': sorted(reports.passed)}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
