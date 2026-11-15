"""Small independent inputs; never clone the repository or run its bad copy."""
from __future__ import annotations

import json
from pathlib import Path


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/disk_retention'
REPOSITORY = Path(__file__).resolve().parents[2]


def tiny_checkout(parent):
    root = parent / 'tiny-ai-poise'
    files = {
        'pyproject.toml': '[project]\nname="ai-poise"\nversion="1"\n',
        'src/pkg/value.py': 'VALUE = 1\n',
        'src/poise/application/check_attempts.py': 'ATTEMPTS = 1\n',
        'tests/test_value.py': 'def test_value(): assert True\n',
        'fixtures/data.json': '{"value":1}\n',
        '.poise-test-cache/canary': 'generated cache\n',
        'projects/unrelated/canary': 'unrelated project\n',
        'tools/.venv/canary': 'unrelated tool\n',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    catalog = root / 'config/testing/test-packages.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes((FIXTURES / 'catalog.json').read_bytes())
    return root, catalog


class TerminalRunner:
    """Exercise the real cache around literal complete process observations."""

    def __init__(self, *, code=0, timeout=False, cancelled=False,
                 child_reaped=True, group_stopped=True):
        self.code, self.timeout, self.cancelled = code, timeout, cancelled
        self.child_reaped, self.group_stopped = child_reaped, group_stopped
        self.calls = 0

    def run(self, run_id, argv, cwd, environment, timeout, stdout, stderr):
        self.calls += 1
        work = Path(argv[argv.index('--basetemp') + 1])
        work.mkdir(parents=True)
        (work / 'work.txt').write_bytes(b'disposable fixture\n')
        stdout.write_bytes(b'fixture stdout\n')
        stderr.write_bytes(b'fixture stderr\n')
        Path(argv[argv.index('--junitxml') + 1]).write_bytes(
            (FIXTURES / 'terminal.xml').read_bytes())
        result = json.loads((FIXTURES / 'terminal.json').read_text())
        result.update(actual_exit_code=self.code, timed_out=self.timeout,
                      cancelled=self.cancelled)
        result['termination']['child_reaped'] = self.child_reaped
        result['termination']['process_group_stopped'] = self.group_stopped
        return result
