"""Actual pytest outcomes cannot be hidden by the evidence projection."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


FIXTURES = Path(__file__).parent / 'fixtures/material_relocation/projection'
CASES = json.loads((FIXTURES / 'cases.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_projection_preserves_actual_outcome_and_refuses_incomplete_proof(tmp_path, case):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / 'test_probe.py'
    source.write_bytes((FIXTURES / case['source']).read_bytes())
    if case.get('plugin'):
        (tmp_path / 'conftest.py').write_bytes((FIXTURES / case['plugin']).read_bytes())
    environment = {**os.environ, 'PYTHONPATH': str(root) + os.pathsep + str(root / 'src')}
    if case.get('addopts'):
        environment['PYTEST_ADDOPTS'] = case['addopts']
    process = subprocess.run([
        sys.executable, '-B', str(FIXTURES / 'probe.py'), str(source),
        str(tmp_path / 'raw'), json.dumps(case['expected_nodes']),
    ], cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert process.returncode == case['effective_exit'], process.stdout + process.stderr
    summary = json.loads(process.stdout)
    assert summary['exit_code'] == case['pytest_exit']
    assert bool(summary['errors']) is case['errors']
    assert (tmp_path / 'raw/pytest.stdout.txt').is_file()
    assert (tmp_path / 'raw/pytest.stderr.txt').is_file()
    if case['id'] == 'pass':
        assert summary['passed'] == ['test_probe.py::test_probe']
        assert summary['failed'] == []
    if case['id'] in ('skip', 'xfail', 'xpass'):
        assert summary['passed'] == []
        # Preserve the existing Reports' raw exception observation as well as
        # the final pytest outcome; neither may become a successful obligation.
        assert summary['failed'] == (['test_probe.py::test_probe'] if case['id'] == 'xfail' else [])
        expected_errors = ['outcome:test_probe.py::test_probe:skip-or-xfail']
        if case['id'] == 'skip':
            expected_errors.append('setup:test_probe.py::test_probe:Skipped')
        assert summary['errors'] == expected_errors
    if case['id'] == 'inventory':
        assert summary['errors'] == ['collection:expected-inventory-mismatch']
