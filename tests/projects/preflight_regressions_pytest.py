"""Scoped selectors and full diagnostics using the accepted pytest classifier."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path

import pytest

from .preflight_pytest import Reports


SUBJECT = 'tests/projects/test_preflight_regressions.py'
CORE = [
    SUBJECT + '::test_wal_journal_refusal_preserves_committed_store[without-shm]',
    SUBJECT + '::test_malformed_task_storage_paths_retain_known_context[database-integer]',
]
GUARDS = [
    SUBJECT + '::test_malformed_task_storage_paths_retain_known_context[database-missing]',
    SUBJECT + '::test_malformed_task_storage_paths_retain_known_context[lock-missing]',
    SUBJECT + '::test_reader_protection_blocks_journal_transition[other-process]',
    SUBJECT + '::test_reader_protection_blocks_journal_transition[same-process]',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', required=True, choices=['core-red', 'guards', 'all'])
    selection = parser.parse_args().selection
    output = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    selectors = CORE if selection == 'core-red' else GUARDS if selection == 'guards' else [SUBJECT]
    reports = Reports()
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = int(pytest.main(['-q', '-rA', *selectors], plugins=[reports]))
    (output / 'pytest.stdout.txt').write_text(stdout.getvalue(), encoding='utf-8')
    (output / 'pytest.stderr.txt').write_text(stderr.getvalue(), encoding='utf-8')
    summary = {'collected': reports.collected, 'errors': sorted(reports.errors),
               'exit_code': code, 'failed': sorted(reports.failed), 'passed': sorted(reports.passed)}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
