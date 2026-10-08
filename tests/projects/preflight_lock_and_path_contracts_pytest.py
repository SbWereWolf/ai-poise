"""Narrow independent pytest projections with complete owner-managed raw output."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path

import pytest

from .preflight_pytest import Reports


SUBJECT = 'tests/projects/test_preflight_lock_and_path_contracts.py'
PARTITIONS = json.loads((Path(__file__).parent / 'fixtures' / 'project_preflight'
                         / 'lock-path-partitions.json').read_text(encoding='utf-8'))
CORE = [
    SUBJECT + '::test_preexisting_caller_sqlite_lock_survives[writer-refusal]',
    SUBJECT + '::test_mandatory_filesystem_input_classification[paths.runtime-integer]',
]
GUARDS = [
    SUBJECT + '::test_supported_filesystem_input_controls',
    SUBJECT + '::test_unexpected_owner_fault_remains_unknown',
    *[SUBJECT + '::test_mandatory_filesystem_input_classification[' + field + '-missing]'
      for field in json.loads((Path(__file__).parent / 'fixtures' / 'project_preflight'
                              / 'lock-path-fields.json').read_text(encoding='utf-8'))],
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', required=True, choices=['core-red', 'guards', *PARTITIONS])
    selection = parser.parse_args().selection
    selectors = CORE if selection == 'core-red' else GUARDS if selection == 'guards' else PARTITIONS[selection]
    output = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    reports = Reports()
    with (output / 'pytest.stdout.txt').open('w', encoding='utf-8') as stdout, \
            (output / 'pytest.stderr.txt').open('w', encoding='utf-8') as stderr:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = int(pytest.main(['-q', '-rA', *selectors], plugins=[reports]))
    summary = {'collected': reports.collected, 'errors': sorted(reports.errors),
               'exit_code': code, 'failed': sorted(reports.failed), 'passed': sorted(reports.passed)}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
