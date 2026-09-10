"""Acceptance of the existing package runner, using real pytest subprocesses.

Failed workspaces are evidence: log paths must remain usable. No test failures
are silently replaced with successful retries. Success requires terminal XML.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_test_packages import fingerprint, parse_junit, run_package


def repository(tmp_path: Path, body: str, conftest: str = '') -> Path:
    root = tmp_path / 'source'
    (root / 'tests').mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n')
    (root / 'tests/test_sample.py').write_text(body)
    if conftest:
        (root / 'tests/conftest.py').write_text(conftest)
    return root


def execute(tmp_path: Path, root: Path, *, success_workspaces: str, timeout: float = 30):
    out = tmp_path / 'report'
    out.mkdir()
    return run_package(root=root, output=out, module=Path('tests/test_sample.py'),
                       index=0, timeout=timeout, success_workspaces=success_workspaces)


def test_failure_retains_workspace_and_original_diagnostic_file(tmp_path):
    root = repository(tmp_path, '''def test_bad(tmp_path):
    (tmp_path / "observed.json").write_text('{"actual_exit_code": 17}')
    assert False, str(tmp_path / "observed.json")
''')
    r = execute(tmp_path, root, success_workspaces='delete')
    assert not r['success'] and r['exit_code'] == 1
    assert r['workspace_retained']
    files = list(Path(r['workspace']).rglob('observed.json'))
    assert len(files) == 1 and json.loads(files[0].read_text())['actual_exit_code'] == 17
    assert r['tests'] == [{'nodeid': 'tests/test_sample.py::test_bad', 'outcome': 'failure'}]
    assert Path(r['stdout']).exists() and Path(r['junit_path']).exists()


@pytest.mark.parametrize('retention,retained', [('keep', True), ('delete', False)])
def test_success_retention_is_explicit_and_preserves_receipts(tmp_path, retention, retained):
    root = repository(tmp_path, '''def test_ok(tmp_path):
    (tmp_path / "receipt.txt").write_text("kept only if requested")
''')
    r = execute(tmp_path, root, success_workspaces=retention)
    assert r['success'] and r['report_error'] is None and not r['timed_out']
    assert r['workspace_retained'] is retained
    assert Path(r['workspace']).exists() is retained
    assert Path(r['stdout']).exists() and Path(r['junit_path']).exists()
    saved = json.loads(Path(r['result_path']).read_text())
    assert saved == r


def test_exit_zero_with_failing_xml_is_not_success(tmp_path):
    root = repository(tmp_path, 'def test_bad():\n    assert False\n',
                      'def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n')
    r = execute(tmp_path, root, success_workspaces='delete')
    assert r['exit_code'] == 0
    assert not r['success'] and r['workspace_retained']
    assert r['tests'][0]['outcome'] == 'failure'


def test_module_timeout_retains_partial_workspace_and_stops_group(tmp_path):
    root = repository(tmp_path, '''import subprocess, sys, time

def test_long(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(100)'])
    (tmp_path / 'child.pid').write_text(str(child.pid))
    time.sleep(100)
''')
    r = execute(tmp_path, root, success_workspaces='delete', timeout=3)
    assert r['timed_out'] and not r['success'] and r['workspace_retained']
    assert r['report_error'] is not None
    pidfiles = list(Path(r['workspace']).rglob('child.pid'))
    assert len(pidfiles) == 1, 'pytest did not reach test body before module deadline'
    stat = Path('/proc') / pidfiles[0].read_text() / 'stat'
    assert not stat.exists() or stat.read_text().split()[2] == 'Z'


@pytest.mark.parametrize('text,reason', [
    (None, 'missing'),
    ('<invalid', 'invalid'),
    ('<testsuites><testsuite tests="0" /></testsuites>', 'empty'),
])
def test_missing_malformed_or_empty_terminal_report_is_explicit(tmp_path, text, reason):
    xml = tmp_path / 'report.xml'
    if text is not None:
        xml.write_text(text)
    rows, error = parse_junit(xml, 'tests/example.py')
    assert rows == [] and reason in error.lower()


def test_junit_preserves_class_identity_for_same_named_methods(tmp_path):
    xml = tmp_path / 'report.xml'
    xml.write_text('''<testsuites><testsuite>
      <testcase classname="tests.test_a.First" name="test_same" file="tests/test_a.py" />
      <testcase classname="tests.test_a.Second" name="test_same" file="tests/test_a.py" />
    </testsuite></testsuites>''')
    rows, error = parse_junit(xml, 'tests/test_a.py')
    assert error is None
    assert [r['nodeid'] for r in rows] == [
        'tests/test_a.py::First::test_same', 'tests/test_a.py::Second::test_same']


def test_existing_package_workspace_is_not_overwritten(tmp_path):
    root = repository(tmp_path, 'def test_ok():\n    pass\n')
    out = tmp_path / 'report'
    out.mkdir()
    kwargs = dict(root=root, output=out, module=Path('tests/test_sample.py'),
                  index=0, timeout=30, success_workspaces='keep')
    first = run_package(**kwargs)
    marker = Path(first['workspace']) / 'do-not-delete'
    marker.write_text('evidence')
    with pytest.raises(FileExistsError):
        run_package(**kwargs)
    assert marker.read_text() == 'evidence'


def test_one_cli_run_keeps_failed_module_and_runs_other_modules(tmp_path):
    root = repository(tmp_path, 'def test_bad(tmp_path):\n    (tmp_path / "why.txt").write_text("cause")\n    assert False\n')
    # Duplicate module basenames must still work in their independent pytest processes.
    (root / 'tests/other').mkdir()
    (root / 'tests/other/test_sample.py').write_text('def test_ok():\n    pass\n')
    output = tmp_path / 'run'
    runner = Path(__file__).resolve().parents[1] / 'tools/run_test_packages.py'
    p = subprocess.run([sys.executable, str(runner), '--root', str(root), '--output', str(output),
                        '--workers', '2', '--timeout', '30', '--success-workspaces', 'delete'],
                       env={**os.environ, 'PYTHONPATH': str(runner.parents[1] / 'src')},
                       text=True, capture_output=True, timeout=50)
    assert p.returncode == 1, p.stdout + p.stderr
    result = json.loads((output / 'result.json').read_text())
    assert result['tests'] == 2 and result['passed'] == 1 and not result['success']
    assert result['unchanged_sources'] and not result['automatic_retries']
    assert len(result['packages']) == 2
    bad = next(p for p in result['packages'] if not p['success'])
    good = next(p for p in result['packages'] if p['success'])
    assert Path(bad['workspace']).exists() and not Path(good['workspace']).exists()


def test_empty_selection_never_reports_success(tmp_path):
    root = repository(tmp_path, 'def test_ok():\n    pass\n')
    (root / 'tests/test_sample.py').unlink()
    runner = Path(__file__).resolve().parents[1] / 'tools/run_test_packages.py'
    p = subprocess.run([sys.executable, str(runner), '--root', str(root), '--output', str(tmp_path/'run'),
                        '--workers', '1', '--timeout', '30', '--success-workspaces', 'delete'],
                       env={**os.environ, 'PYTHONPATH': str(runner.parents[1] / 'src')},
                       text=True, capture_output=True, timeout=20)
    assert p.returncode != 0 and 'no test modules' in (p.stdout+p.stderr).lower()


def test_source_fingerprint_includes_the_checking_tools(tmp_path):
    root = repository(tmp_path, 'def test_ok():\n    pass\n')
    (root / 'tools').mkdir()
    script = root / 'tools/runner.py'
    script.write_text('print("before")\n')
    before = fingerprint(root)
    script.write_text('print("after")\n')
    after = fingerprint(root)
    assert before != after and 'tools/runner.py' in before
