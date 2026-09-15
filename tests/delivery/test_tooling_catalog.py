"""C027.1: declared AI-poise tooling checks, using the existing package owner."""
from pathlib import Path
import os
import subprocess
import sys

import pytest
from poise.infrastructure.test_packages import AiPoiseTestPackages

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'config/testing/test-packages.json'


def test_tooling_packages_declare_fast_and_recovery_checks_without_another_runner():
    catalog = AiPoiseTestPackages.load(CATALOG)
    fast = catalog.membership(ROOT, 'tooling-fast')
    recovery = catalog.membership(ROOT, 'tooling-recovery')
    assert fast['owner'] == recovery['owner'] == 'repository-tooling'
    assert 'tests/delivery/test_tooling_catalog.py' in fast['members']['tests']
    assert 'tests/delivery/test_work_checkpoint.py' in recovery['members']['tests']
    assert 'tests/test_package_execution.py' in recovery['members']['tests']
    assert set(fast['members']['tests']).isdisjoint(recovery['members']['tests'])
    assert 'tools/work_checkpoint.py' in fast['members']['source']
    assert 'tools/run_test_packages.py' in recovery['members']['source']
    assert 'verification-runner' in recovery['integration_boundaries']
    assert set(fast) == {'id', 'owner', 'integration_boundaries', 'members'}


@pytest.mark.parametrize('path', sorted(ROOT.glob('tools/*.py')), ids=lambda p:p.name)
def test_shipped_python_tool_parses_without_executing_it(path):
    # Includes maintenance/mutation tools, but never executes them on live code.
    compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)


@pytest.mark.parametrize('tool', ['work_checkpoint.py', 'run_ai_poise_test_package.py',
                                  'run_test_packages.py', 'run_slice_tests.py'])
def test_supported_cli_help_works_from_an_arbitrary_filesystem_directory(tmp_path, tool):
    # Candidate tool under test; this does not alter live hook configuration.
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / tool), '--help'],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH':str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
    assert list(tmp_path.iterdir()) == []


def test_smoke_launcher_has_valid_shell_syntax():
    result = subprocess.run(['bash', '-n', str(ROOT / 'tests/smoke.sh')],
                            cwd=ROOT, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
