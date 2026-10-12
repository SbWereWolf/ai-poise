"""Existing pytest evidence projection with strict, test-owned collection coverage."""
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path

import pytest

from .preflight_pytest import Reports


FIXTURES = Path(__file__).parent / 'fixtures/material_relocation'
FILES = [
    'tests/projects/test_state_material_relocation.py',
    'tests/projects/test_material_relocation_remediation.py',
    'tests/projects/test_material_relocation_coordination.py',
    'tests/projects/test_runtime_resources_bootstrap.py',
    'tests/verification/test_material_relocation_consumers.py',
]
PARTITION_COUNT = 7


def partition_inventory(inventory):
    """Partition the unchanged literal obligations; collection still proves each ID."""
    if (not isinstance(inventory, list) or len(inventory) != 93
            or any(not isinstance(node, str) or not node.strip() for node in inventory)
            or len(set(inventory)) != len(inventory)):
        raise ValueError('Invalid literal material relocation inventory')
    ordered = sorted(inventory)
    return [ordered[index::PARTITION_COUNT] for index in range(PARTITION_COUNT)]


class CompleteReports(Reports):
    def __init__(self, expected):
        super().__init__()
        self.expected = sorted(expected)

    def pytest_collection_finish(self, session):
        super().pytest_collection_finish(session)
        if (self.collected != self.expected or not self.collected
                or len(self.expected) != len(set(self.expected))):
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
    if not reports.errors:
        completed = reports.passed + reports.failed
        if Counter(completed) != Counter(expected) or len(completed) != len(set(completed)):
            reports.errors.append('execution:completed-inventory-mismatch')
        elif code == 0 and sorted(reports.passed) != sorted(expected):
            reports.errors.append('execution:green-inventory-mismatch')
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
    parser.add_argument('--selection', required=True, choices=['red', 'all', 'partition'])
    parser.add_argument('--partition', type=int, choices=range(1, PARTITION_COUNT + 1))
    args = parser.parse_args()
    if (args.selection == 'partition') != (args.partition is not None):
        parser.error('--partition is required exactly for --selection partition')
    inventory = 'all' if args.selection == 'partition' else args.selection
    expected = json.loads((FIXTURES / (inventory + '-nodes.json')).read_text())
    if args.selection == 'partition':
        expected = partition_inventory(expected)[args.partition - 1]
    selectors = FILES if args.selection == 'all' else expected
    code, summary = run(selectors, expected, Path(os.environ['POISE_RUN_OUTPUT_DIR']))
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
