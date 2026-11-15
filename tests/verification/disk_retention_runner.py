"""Exact RED projection; reuse the existing complete pytest lifecycle recorder."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.actions.lifecycle_reporting import LifecycleReport


def main():
    lifecycle = LifecycleReport()
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = pytest.main([*sys.argv[1:], '-q', '--tb=short', '-p', 'no:cacheprovider'],
                           plugins=[lifecycle])
    output = Path(os.environ.get('POISE_RUN_OUTPUT_DIR',
                                os.environ.get('DISK_RETENTION_OUTPUT', '/tmp/disk-0002-retention-check')))
    output.mkdir(parents=True, exist_ok=True)
    (output / 'pytest.txt').write_text(stdout.getvalue() + stderr.getvalue(), encoding='utf-8')
    errors = {item['nodeid'] + ':collection': item['diagnostic']
              for item in lifecycle.collection}
    failures, passed = {}, 0
    for report in lifecycle.reports:
        if report['phase'] == 'call' and report['outcome'] == 'passed':
            passed += 1
        elif report['outcome'] in ('failed', 'skipped'):
            message = report['diagnostic'].splitlines()[0]
            if report['phase'] == 'call' and message.startswith('AssertionError:'):
                failures[report['nodeid']] = message
            else:
                errors[report['nodeid'] + ':' + report['phase']] = message
    payload = {'errors': errors, 'failures': failures,
               'passed': passed, 'tests_run': len(lifecycle.collected)}
    if code not in (0, 1) or errors:
        sys.stderr.write(stdout.getvalue() + stderr.getvalue())
    print(json.dumps(payload, sort_keys=True, separators=(',', ':')))
    return int(code)


if __name__ == '__main__':
    raise SystemExit(main())
