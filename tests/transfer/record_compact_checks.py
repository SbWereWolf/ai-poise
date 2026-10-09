"""Compose standard pytest with the existing complete-phase reporting plugin.

Keep pytest's exit unchanged and preserve its full diagnostic stream. This
adapter has no expected-failure classifier; the Poise method owns expectations.
"""
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from actions.lifecycle_reporting import LifecycleReport


if __name__ == '__main__':
    destination = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    destination.mkdir(parents=True, exist_ok=True)
    observer = LifecycleReport()
    with (destination / 'pytest.stdout.txt').open('w') as stdout:
        with (destination / 'pytest.stderr.txt').open('w') as stderr:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = pytest.main(['-q', '--tb=short', *sys.argv[1:]], plugins=[observer])
    summary = json.dumps(observer.summary(code), ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'))
    (destination / 'summary.json').write_text(summary + '\n')
    print(summary)
    raise SystemExit(int(code))
