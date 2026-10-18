"""Literal output-record and nested-proof boundaries missing from the first fix."""
import shlex

import pytest

from tests.command_intake.helpers import fixture
from tests.command_intake.test_projection_regressions import (
    actual, allow, exact_coverage, intake,
)


DATA = fixture('remaining-boundaries.json')


@pytest.fixture
def boundary_intake(intake):
    # Reuse the accepted isolated public transports and actual End/recover.
    for path, body in DATA['files'].items():
        intake.s.write(path, body.encode('utf-8'))
    return intake


@pytest.mark.parametrize('row', DATA['reverse'], ids=lambda row: row['id'])
def test_reverse_output_records(row, boundary_intake):
    i = boundary_intake
    i.mode(row['mode'])
    i.configure(**{'classifier.bounded_reads.max_bytes_per_file': row['per_file_bytes'],
                   'classifier.bounded_reads.max_total_bytes': row['total_bytes'],
                   'classifier.bounded_reads.max_lines_per_file': row['per_file_lines'],
                   'classifier.bounded_reads.max_total_lines': row['total_lines']})
    call, native, pre = i.pre(row['command'])
    if row['reason'] is not None:
        assert pre['decision']['reason_code'] == row['reason'], pre
        assert pre['decision']['action'] == ('deny' if row['mode'] == 'enforce' else 'audit')
        if row['mode'] == 'enforce':
            assert pre['status'] == 'denied'
            assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
            assert i.general_snapshot() == {'events': [], 'calls': []}
            i.counts(pending=0, current=0)
            assert i.coverage()['details']['documents'] == []
            return
    else:
        assert pre['decision']['action'] == ('none' if row.get('empty') else 'allow'), pre
    assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
    response = actual(i, call, row['stdout'])
    assert len(response['stdout'].encode('utf-8')) == row['output_bytes']
    post_native, post = i.post(call, response)
    if row.get('empty'):
        assert post_native.get('continue') is not False
        assert post_native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
        assert post['counts']['registered'] == 0
        assert i.general_snapshot() == {'events': [], 'calls': []}
    else:
        assert post['status'] == 'settled'
        assert post['counts']['registered'] == len(row['coverage'])
    exact_coverage(i, call, row['coverage'])


@pytest.mark.parametrize('row', DATA['cwd'], ids=lambda row: row['id'])
def test_nested_first_directory_change(row, boundary_intake):
    i = boundary_intake
    i.mode(row['mode'])
    i.configure(**{'classifier.unknown_expansion.deny_nested_shell_readers': False})
    script = row['script'].replace('__ABS_ROOT__', shlex.quote(str(i.s.checkout / 'same.txt')))
    for _ in range(row['depth']):
        script = 'bash -c ' + shlex.quote(script)
    if row.get('outer_cd'):
        script = 'cd sub && ' + script
    call, native, pre = i.pre(script)
    if row['unresolved']:
        assert pre['decision']['reason_code'] == 'unresolved_execution_cwd', pre
        assert pre['decision']['action'] == ('deny' if row['mode'] == 'enforce' else 'audit')
        if row['mode'] == 'enforce':
            assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
        else:
            assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
            response = actual(i, call, 'SUB01\nSUB02\n')
            post_native, _ = i.post(call, response)
            assert post_native.get('continue') is not False
            assert post_native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
        assert i.general_snapshot() == {'events': [], 'calls': []}
        assert i.coverage()['details']['documents'] == []
        call, _ = allow(i, 'head -n2 same.txt')
    else:
        assert pre['decision']['action'] == 'allow', pre
        assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
    response = actual(i, call, 'ROOT01\nROOT02\n')
    assert i.post(call, response)[1]['status'] == 'settled'
    exact_coverage(i, call, {'same.txt': [[1, 2]]})
