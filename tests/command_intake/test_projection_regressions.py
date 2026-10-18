"""Additional public-boundary oracles for the four F05 inspection findings.

Expected source coordinates and wire outcomes are test-owned literals. These
tests do not modify the original 62-case catalog or use production classifiers
to manufacture expected results.
"""
from copy import deepcopy
import json
import shlex
import sqlite3
import subprocess

import pytest

from tests.command_intake.helpers import Intake, fixture, set_field
from tests.content_reader.helpers import other_project


DATA = fixture('projection-regressions.json')


@pytest.fixture(params=['sdk', 'cli'])
def intake(project, tmp_path, monkeypatch, request):
    root = tmp_path / request.param
    root.mkdir()
    selected = other_project(project, root)
    value = Intake(selected, root, monkeypatch, request.param)
    value.configure(**{'registry.retention.max_pending_age_seconds': 60})
    value.enable()
    for path, body in DATA['files'].items():
        value.s.write(path, body.encode('utf-8'))
    try:
        yield value
    finally:
        value.s.event('SessionEnd')
        assert value.s.invoke({'operation': 'recover'})[0] == 0


def actual(i, call, expected, exit_code=0):
    response = i.actual(call)
    assert response == {'stdout': expected, 'stderr': '', 'exit_code': exit_code}
    return response


def allow(i, command, **kwargs):
    call, native, pre = i.pre(command, **kwargs)
    assert pre['decision']['action'] in {'allow', 'audit'}, pre
    assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
    return call, pre


def exact_coverage(i, call, expected, *, complete=False):
    plans = i.plans(call)
    observed = {path: plan['line_ranges'] for _, path, _, plan in plans}
    assert len(plans) == len(expected) and observed == expected, plans
    for _, _, _, plan in plans:
        assert plan['coverage_kind'] == ('complete' if complete else 'lines')
    docs = i.coverage()['details']['documents']
    assert {d['path']: d['ranges'] for d in docs} == expected, docs
    assert all(d['complete'] is complete for d in docs), docs


@pytest.mark.parametrize('row', DATA['coordinates'], ids=lambda row: row['id'])
def test_source_coordinates(row, intake):
    i = intake
    i.mode(row['mode'])
    call, _ = allow(i, row['command'])
    response = actual(i, call, row['stdout'])
    _, post = i.post(call, response)
    assert post['status'] == 'settled', post
    assert post['counts']['registered'] == len(row['coverage'])
    exact_coverage(i, call, row['coverage'], complete=row.get('complete', False))
    if 'followup' in row:
        follow, _ = allow(i, row['followup'])
        response = actual(i, follow, row['followup_stdout'])
        assert i.post(follow, response)[1]['status'] == 'settled'


@pytest.mark.parametrize('mode', ['enforce', 'audit'])
def test_unproved_reverse_coordinates(mode, intake):
    """RP06: a bounded observation cannot prove a 500-line file's end."""
    i = intake
    i.mode(mode)
    call, native, pre = i.pre('tac docs/long.txt | head -n2')
    if mode == 'enforce':
        assert pre['decision']['action'] == 'deny', pre
        assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
        i.counts(pending=0, current=0)
    else:
        assert pre['decision']['action'] == 'audit', pre
        assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
        response = actual(i, call, 'L500\nL499\n')
        i.post(call, response)
        assert i.coverage()['details']['documents'] == []
    assert i.plans(call) == []


@pytest.mark.parametrize('mode', ['enforce', 'audit'])
def test_changed_cwd_does_not_credit_root(mode, intake):
    """RP07: after a second cd, the old leading base proves no relative path."""
    i = intake
    i.mode(mode)
    command = i.command('cd sub && head -n2 same.txt')
    call, native, pre = i.pre(command, raw=True)
    assert pre['decision']['reason_code'] == 'unresolved_execution_cwd', pre
    if mode == 'enforce':
        assert pre['decision']['action'] == 'deny'
        assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
    else:
        assert pre['decision']['action'] == 'audit'
        assert native['hookSpecificOutput']['permissionDecision'] == 'allow'
        response = actual(i, call, 'SUB01\nSUB02\n')
        i.post(call, response)
    assert i.general_snapshot() == {'events': [], 'calls': []}
    assert i.coverage()['details']['documents'] == []
    follow, _ = allow(i, 'head -n2 same.txt')
    response = actual(i, follow, 'ROOT01\nROOT02\n')
    assert i.post(follow, response)[1]['status'] == 'settled'
    exact_coverage(i, follow, {'same.txt': [[1, 2]]})


@pytest.mark.parametrize('shape', ['leading-cd', 'absolute', 'absolute-after-cd'])
def test_proven_cwd_controls(shape, intake):
    """RP08: absolute operands remain independent of cwd transitions."""
    i = intake
    operand = shlex.quote(str(i.s.checkout / 'same.txt'))
    commands = {'leading-cd': i.command('head -n2 same.txt'),
                'absolute': 'head -n2 ' + operand,
                'absolute-after-cd': i.command('cd sub && head -n2 ' + operand)}
    call, _ = allow(i, commands[shape], raw=True)
    response = actual(i, call, 'ROOT01\nROOT02\n')
    assert i.post(call, response)[1]['status'] == 'settled'
    exact_coverage(i, call, {'same.txt': [[1, 2]]})


def test_registered_checkout_control(intake, tmp_path):
    """RP08: explicit registered checkout B wins over native event cwd A."""
    i = intake
    original = i.s.checkout
    original_key = i.coverage()['context']['checkout_key']
    target = tmp_path / 'registered B'
    cp = subprocess.run(['git', '-C', str(original), 'worktree', 'add', '-b',
                         'projection-copy', str(target)], capture_output=True, check=False)
    assert cp.returncode == 0, cp.stderr
    i.s.checkout = target
    i.s.write('same.txt', b'OTHER01\nOTHER02\nOTHER03\n')
    try:
        call, pre = allow(i, i.command('head -n2 same.txt'), raw=True, cwd=str(original))
        assert pre['context']['checkout_key'] != original_key
        response = actual(i, call, 'OTHER01\nOTHER02\n')
        assert i.post(call, response)[1]['status'] == 'settled'
        exact_coverage(i, call, {'same.txt': [[1, 2]]})
    finally:
        i.s.checkout = original
    assert i.coverage()['details']['documents'] == []


def stored(i, call):
    with sqlite3.connect(i.s.db.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT plans_json FROM pending_calls WHERE tool_use_id=?',
                         (call,)).fetchone()
    assert row is not None
    return row[0]


def replace_stored(i, call, value):
    # Only the isolated test's general registry; never managed Task data.
    with sqlite3.connect(i.s.db) as db:
        changed = db.execute('UPDATE pending_calls SET plans_json=? WHERE tool_use_id=?',
                             (value, call)).rowcount
    assert changed == 1


@pytest.mark.parametrize('row', DATA['corruptions'], ids=lambda row: row['id'])
def test_cached_control_validation(row, intake):
    """RP09/10: validate known nested cached controls before public replay."""
    i = intake
    call, _ = allow(i, 'head -n2 docs/pipeline.txt')
    response = actual(i, call, 'P01\nP02\n')
    if row['slot'] == 'post_result':
        assert i.post(call, response)[1]['status'] == 'settled'
    record = json.loads(stored(i, call))
    if row['mutation'] == 'empty':
        record[row['slot']] = {}
    elif row['mutation'] == 'missing-path':
        del record[row['slot']]['response_path']
    else:
        value = row['value']
        if value == '__test_owned_wrong_path__':
            value = str(i.root / 'foreign-control.json')
            (i.root / 'foreign-control.json').write_bytes(b'unchanged sentinel\n')
        set_field(record[row['slot']], row['field'], value)
    corrupt = json.dumps(record, ensure_ascii=False, sort_keys=True)
    replace_stored(i, call, corrupt)
    before = i.general_snapshot()
    if row['phase'] == 'pre':
        native, result = i.hook(deepcopy(i.calls[call]))
        assert result['status'] == 'denied'
        assert result['decision']['action'] == 'deny'
        assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
    else:
        native, result = i.post(call, response)
        assert result['status'] == 'unconfirmed'
        assert result['decision']['action'] == 'none'
        assert native['systemMessage']
        assert native.get('continue') is not False
        assert native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    assert result['error']['code'] == 'registry_invalid', result
    assert result['error']['message'] and result['error']['action']
    assert result['counts']['registered'] == 0
    assert stored(i, call) == corrupt
    assert i.general_snapshot() == before
    if row.get('value') == '__test_owned_wrong_path__':
        assert (i.root / 'foreign-control.json').read_bytes() == b'unchanged sentinel\n'


@pytest.mark.parametrize('state', ['settled', 'discarded', 'later-generation', 'deduplicated'])
def test_healthy_cached_controls(state, intake):
    """RP11: valid replay, historical generation and nonadditive counts survive."""
    i = intake
    command = 'head -n2 docs/pipeline.txt'
    if state == 'discarded':
        command += ' && false'
    elif state == 'deduplicated':
        command += ' && head -n2 docs/pipeline.txt'
    call, pre = allow(i, command)
    assert i.hook(deepcopy(i.calls[call]))[1] == pre
    body = 'P01\nP02\nP01\nP02\n' if state == 'deduplicated' else 'P01\nP02\n'
    response = actual(i, call, body, exit_code=1 if state == 'discarded' else 0)
    _, post = i.post(call, response)
    assert post['status'] == ('discarded' if state == 'discarded' else 'settled')
    if state == 'deduplicated':
        assert post['counts'] == {'eligible': 2, 'skipped': 0, 'registered': 1}
    before = i.general_snapshot()
    if state == 'later-generation':
        i.advance()
    assert i.hook(deepcopy(i.calls[call]))[1] == pre
    assert i.post(call, response)[1] == post
    assert i.general_snapshot() == before


@pytest.mark.parametrize('phase', ['pre', 'post'])
def test_corrupt_json_control(phase, intake):
    """RP12: the preexisting invalid-JSON rejection is a separate GREEN guard."""
    i = intake
    call, _ = allow(i, 'head -n2 docs/pipeline.txt')
    response = actual(i, call, 'P01\nP02\n')
    replace_stored(i, call, 'not-json')
    before = i.general_snapshot()
    if phase == 'pre':
        native, result = i.hook(deepcopy(i.calls[call]))
        assert result['status'] == 'denied'
        assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
    else:
        native, result = i.post(call, response)
        assert result['status'] == 'unconfirmed' and native['systemMessage']
        assert native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    assert result['error']['code'] == 'registry_invalid'
    assert stored(i, call) == 'not-json'
    assert i.general_snapshot() == before
