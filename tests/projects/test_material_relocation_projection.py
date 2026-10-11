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


def literal_inventory():
    return json.loads((FIXTURES.parent / 'all-nodes.json').read_text())


def test_partitions_cover_all_literal_obligations_once():
    from projects.material_relocation_pytest import partition_inventory
    inventory = literal_inventory()
    groups = partition_inventory(inventory)
    assert len(inventory) == 93
    assert len(groups) == 7
    assert sorted(map(len, groups)) == [13, 13, 13, 13, 13, 14, 14]
    flattened = [node for group in groups for node in group]
    assert len(flattened) == len(set(flattened)) == 93
    assert set(flattened) == set(inventory)
    assert all(group for group in groups)


@pytest.mark.parametrize('fault', ['empty', 'missing', 'extra', 'duplicate', 'non-string'])
def test_partition_inventory_refuses_invalid_catalogue(fault):
    from projects.material_relocation_pytest import partition_inventory
    inventory = literal_inventory()
    if fault == 'empty':
        inventory = []
    elif fault == 'missing':
        inventory.pop()
    elif fault == 'extra':
        inventory.append('unregistered::test_extra')
    elif fault == 'duplicate':
        inventory[-1] = inventory[0]
    else:
        inventory[-1] = None
    with pytest.raises(ValueError, match='inventory'):
        partition_inventory(inventory)


@pytest.mark.parametrize('arguments', [
    ['--selection', 'partition'],
    ['--selection', 'partition', '--partition', '0'],
    ['--selection', 'partition', '--partition', '8'],
    ['--selection', 'all', '--partition', '1'],
    ['--selection', 'red', '--partition', '1'],
])
def test_partition_cli_requires_exact_explicit_selection(tmp_path, arguments):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / 'raw'
    process = subprocess.run([
        sys.executable, '-B', '-m', 'tests.projects.material_relocation_pytest', *arguments,
    ], cwd=root, env={**os.environ, 'PYTHONPATH': str(root / 'src'),
                     'POISE_RUN_OUTPUT_DIR': str(output)}, capture_output=True, text=True)
    assert process.returncode == 2
    assert 'partition' in process.stderr
    assert not output.exists()


def test_partition_cli_routes_every_obligation_to_the_existing_runner(monkeypatch, tmp_path):
    from projects import material_relocation_pytest as projection
    observed = []

    def observe(selectors, expected, output):
        assert selectors == expected
        assert output == tmp_path
        assert expected
        observed.extend(expected)
        return 0, {'transport_probe': True}

    monkeypatch.setattr(projection, 'run', observe)
    monkeypatch.setenv('POISE_RUN_OUTPUT_DIR', str(tmp_path))
    for index in range(1, 8):
        monkeypatch.setattr(sys, 'argv', ['projection', '--selection', 'partition',
                                        '--partition', str(index)])
        assert projection.main() == 0
    assert len(observed) == len(set(observed)) == 93
    assert set(observed) == set(literal_inventory())


def test_partition_collect_only_cannot_be_green(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / 'raw'
    process = subprocess.run([
        sys.executable, '-B', '-m', 'tests.projects.material_relocation_pytest',
        '--selection', 'partition', '--partition', '1',
    ], cwd=root, env={**os.environ, 'PYTHONPATH': str(root / 'src'),
                     'PYTEST_ADDOPTS': '--collect-only',
                     'POISE_RUN_OUTPUT_DIR': str(output)}, capture_output=True, text=True)
    assert process.returncode == 2, process.stdout + process.stderr
    summary = json.loads(process.stdout)
    assert summary['exit_code'] == 0
    assert len(summary['collected']) == 14
    assert summary['passed'] == summary['failed'] == []
    assert summary['errors'] == ['execution:completed-inventory-mismatch']
    assert (output / 'pytest.stdout.txt').is_file()
    assert (output / 'pytest.stderr.txt').is_file()
