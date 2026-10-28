"""Real child results, synchronous completion and independent new intentions."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('kind', ['success', 'failure'])
def test_actual_terminal_outcome_and_full_streams(case, kind):
    expected = case.body[kind]
    case.child(code=expected['exit'])
    configured_bytes = case.policy_path.read_bytes()
    peer = case.peer(); peer.initialize()
    envelope, body = case.full_run(peer)
    record = case.record(body)
    assert body['result']['status'] == record['status'] == expected['status']
    assert envelope['isError'] is expected['isError']
    assert record['actual_exit_code'] == expected['exit']
    case.assert_streams(record)
    assert record['termination']['capture_complete'] is True
    assert body['invocation']['state'] == 'terminal' and body['invocation']['id']
    assert len([r for r in case.rows() if r['event'] == 'STARTED']) == 1
    assert Path(body['result']['response_path']).is_file()
    assert case.policy_path.read_bytes() == configured_bytes


def test_reader_accepts_ping_while_run_is_held(case):
    case.child(mode='hold')
    peer = case.peer(); peer.initialize()
    peer.call('held', 'run', case.arguments())
    started = case.started()
    peer.request('ping', 'ping', {})
    assert peer.response('ping')['result'] == {}
    assert 'held' not in peer.pending
    assert not [row for row in case.rows() if row['event'] == 'EXIT']
    case.release()
    _, body = peer.outcome('held')
    assert body['result']['status'] == 'passed'
    assert case.record(body)['actual_exit_code'] == 0 and started['pid']


def test_preview_has_no_active_or_diagnostic_effect(case):
    peer = case.peer(); peer.initialize()
    before = case.scene.functional_state()
    _, body = case.full_run(peer, arguments=case.arguments(preview=True))
    assert body['operation'] == 'preview'
    assert body['invocation']['id'] is None and body['invocation']['state'] == 'not_started'
    assert body['result']['status'] == 'planned'
    assert not case.rows() and case.scene.functional_state() == before


def test_two_independent_intentions_execute_twice(case):
    peer = case.peer(); peer.initialize()
    _, first = case.full_run(peer, 'first')
    _, second = case.full_run(peer, 'second')
    assert len([row for row in case.rows() if row['event'] == 'STARTED']) == 2
    assert first['invocation']['id'] != second['invocation']['id']
    assert case.record(first)['run_id'] != case.record(second)['run_id']


@pytest.mark.parametrize('stream', ['empty', 'large'])
def test_empty_and_large_unclipped_machine_output(case, stream):
    case.stdout_file.write_bytes(b'' if stream == 'empty' else ('Иван🐺\n' * 6000).encode())
    case.stderr_file.write_bytes(b'' if stream == 'empty' else b'error\n' * 5000)
    peer = case.peer(); peer.initialize()
    _, body = case.full_run(peer)
    case.assert_streams(case.record(body))
    assert case.record(body)['termination']['capture_complete'] is True
