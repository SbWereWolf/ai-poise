from pathlib import Path
import os
import sys

import pytest

from poise.common import PoiseError
from poise.execution import capture_declared_outputs
from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry
from conftest import verification_plan


def method(outputs):
    return {
        'id': 'OUTPUT',
        'argv': [sys.executable, '-c', 'print("OK")'],
        'cwd': '.',
        'environment': {},
        'expected_exit_code': 0,
        'stdout_contains': ['OK'],
        'stderr_contains': [],
        'source_under_test': {'kind': 'repository', 'bindings': [{'kind': 'cwd', 'path': '.'}]},
        'verification_plan': verification_plan(
            'Verify declared output capture.', ['src/**'], green_stages=['green']
        ),
        'outputs': outputs,
    }


def registry(value):
    return CheckRegistry.from_task([value], {'green': ['OUTPUT']}, ('green',))


def test_output_declarations_are_part_of_registered_method_contract():
    registry(method([{'id': 'report', 'path': 'reports/result.json', 'required': True}]))
    for outputs in (
        [{'id': 'report/path', 'path': 'report.json', 'required': True}],
        [{'id': 'report', 'path': '../report.json', 'required': True}],
        [{'id': 'report', 'path': 'report.json', 'required': 'yes'}],
    ):
        with pytest.raises(DomainError):
            registry(method(outputs))


def test_capture_snapshots_required_output_into_run_evidence(tmp_path):
    output_dir = tmp_path / 'runner-output'
    output_dir.mkdir()
    (output_dir / 'reports').mkdir()
    source = output_dir / 'reports/result.json'
    source.write_text('{"ok":true}')
    records, complete = capture_declared_outputs(
        [{'id': 'report', 'path': 'reports/result.json', 'required': True}],
        output_dir,
        tmp_path / 'run/outputs',
    )
    assert complete
    record = records[0]
    captured = Path(record['path'])
    assert record['status'] == 'captured' and captured.read_text() == source.read_text()
    source.write_text('{"changed":true}')
    assert captured.read_text() == '{"ok":true}'
    assert record['size'] == captured.stat().st_size


def test_required_missing_output_fails_while_optional_missing_is_valid(tmp_path):
    output_dir = tmp_path / 'runner-output'
    output_dir.mkdir()
    required, complete = capture_declared_outputs(
        [{'id': 'report', 'path': 'missing.json', 'required': True}], output_dir, tmp_path / 'r1'
    )
    assert not complete and required[0]['status'] == 'missing'
    optional, complete = capture_declared_outputs(
        [{'id': 'trace', 'path': 'missing.trace', 'required': False}], output_dir, tmp_path / 'r2'
    )
    assert complete and optional[0]['status'] == 'missing'


def test_output_symlink_cannot_escape_runner_output_directory(tmp_path):
    output_dir = tmp_path / 'runner-output'
    output_dir.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('secret')
    (output_dir / 'report.txt').symlink_to(outside)
    with pytest.raises(PoiseError, match='escapes'):
        capture_declared_outputs(
            [{'id': 'report', 'path': 'report.txt', 'required': True}], output_dir, tmp_path / 'run'
        )
