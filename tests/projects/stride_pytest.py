"""Use the existing pytest evidence projection; retain complete raw diagnostics."""
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path

import pytest

from .preflight_pytest import Reports


def main():
    output = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    reports = Reports()
    with (output / 'pytest.stdout.txt').open('w', encoding='utf-8') as stdout, \
            (output / 'pytest.stderr.txt').open('w', encoding='utf-8') as stderr:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = int(pytest.main(['-q', '-rA', 'tests/projects/test_stride_launcher.py'],
                                   plugins=[reports]))
    summary = {'collected': reports.collected, 'errors': sorted(reports.errors),
               'exit_code': code, 'failed': sorted(reports.failed),
               'passed': sorted(reports.passed)}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
