"""Cancellation actually reaches owned processes; snapshots alone cannot pass."""
from pathlib import Path

import pytest

from tests.named_checks.support import write_json


def held(case, mode='term', *, descendant=False):
    case.child(mode=mode, descendant=descendant)
    peer = case.peer(); peer.initialize()
    peer.call('run', 'run', case.arguments())
    case.started()
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    assert preview['status'] == 'planned'
    identity = preview['target']['invocation_id']
    assert identity and preview['termination']['graceful_requested'] is False
    assert preview['termination']['force_requested'] is False
    assert not [row for row in case.rows() if row['event'] == 'TERM']
    assert 'run' not in peer.pending
    return peer, identity


def test_cancel_preview_then_real_graceful_completion(case):
    peer, identity = held(case)
    envelope, cancelled = case.cancel_outcome(peer, identity=identity)
    assert envelope['isError'] is False and cancelled['status'] == 'cancelled'
    assert cancelled['target']['invocation_id'] == identity
    assert cancelled['termination']['graceful_requested'] is True
    assert cancelled['termination']['force_requested'] is False
    assert cancelled['termination']['process_group_stopped'] is True
    assert cancelled['termination']['child_reaped'] is True
    assert cancelled['termination']['capture_complete'] is True
    _, body = peer.outcome('run')
    record = case.record(body)
    assert record['status'] == 'cancelled' and record['actual_exit_code'] == 17
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']
    assert Path(record['stdout']['path']).read_bytes() == case.stdout_file.read_bytes() + b'gracefully stopped\n'
    assert Path(record['stderr']['path']).read_bytes() == case.stderr_file.read_bytes()
    assert record['termination']['capture_complete'] is True


def test_ignored_term_forces_owned_group_and_preserves_full_capture(case):
    peer, identity = held(case, 'ignore', descendant=True)
    assert case.started()['descendant_pid']
    _, cancelled = case.cancel_outcome(peer, identity=identity)
    assert cancelled['status'] == 'cancelled'
    assert cancelled['termination'] == case.body['forced_termination']
    _, body = peer.outcome('run')
    record = case.record(body)
    assert record['actual_exit_code'] == -9 and record['status'] == 'cancelled'
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']
    case.assert_streams(record)


def test_natural_completion_is_preserved_by_repeated_cancel(case):
    peer = case.peer(); peer.initialize()
    _, completed = case.full_run(peer)
    path = Path(completed['result']['response_path'])
    saved = path.read_bytes()
    identity = completed['invocation']['id']
    for request in ['late', 'again']:
        _, cancelled = case.cancel_outcome(peer, request, identity=identity)
        assert cancelled['status'] == 'complete'
        assert cancelled['termination']['graceful_requested'] is False
        assert cancelled['termination']['force_requested'] is False
        assert path.read_bytes() == saved
    assert [row['event'] for row in case.rows()] == ['STARTED', 'EXIT']


def test_another_caller_cannot_cancel_live_target(case):
    owner, identity = held(case)
    binding = case.root / 'foreign-caller.json'
    write_json(binding, {
        'schema': 'poise-caller-binding-1', 'caller_id': 'f13-foreign-independent-caller'})
    environment = {**case.environment, 'POISE_CALLER_BINDING': str(binding)}
    foreign = case.peer('foreign', environment=environment); foreign.initialize()
    _, refused = case.cancel_outcome(foreign, identity=identity)
    assert refused['status'] == 'denied'
    assert refused['termination']['graceful_requested'] is False
    assert refused['termination']['force_requested'] is False
    assert not [row for row in case.rows() if row['event'] == 'TERM']
    owner.request('still-working', 'ping', {})
    assert owner.response('still-working')['result'] == {}
    case.release()
    _, completed = owner.outcome('run')
    assert completed['result']['status'] == 'passed'


def test_independent_live_runs_are_not_globally_banned(case):
    case.child(marker='one', mode='term', name='one')
    case.child(marker='two', mode='hold', name='two')
    peer = case.peer(); peer.initialize()
    peer.call('one', 'run', case.arguments(name='one'))
    first = case.started('one')
    peer.call('two', 'run', case.arguments(name='two'))
    second = case.started('two')
    assert first['pid'] != second['pid']
    _, ambiguous = case.cancel_outcome(peer, 'ambiguous', preview=True)
    assert ambiguous['status'] == 'ambiguous'
    assert not [row for marker in ['one', 'two'] for row in case.rows(marker) if row['event'] == 'TERM']
    case.release('one')
    _, one = peer.outcome('one')
    assert one['result']['status'] == 'passed'
    _, preview = case.cancel_outcome(peer, 'sole-preview', preview=True)
    assert preview['status'] == 'planned'
    _, cancelled = case.cancel_outcome(peer, 'only-two', identity=preview['target']['invocation_id'])
    assert cancelled['status'] == 'cancelled'
    _, two = peer.outcome('two')
    assert case.record(two)['status'] == 'cancelled'
    assert [row['event'] for row in case.rows('one')] == ['STARTED', 'EXIT']


def test_composite_cancellation_never_starts_remaining_steps(case):
    case.child(mode='term')
    # A suite has explicit, distinct profile markers and no test-private runner.
    case.child(marker='second', name='second')
    suite = case.scene.suites['suites'][0]
    suite['commands'] = [case.scene.selection(), case.scene.selection('second')]
    case.scene.save()
    peer = case.peer(); peer.initialize()
    args = case.arguments(); args['selection'] = {'kind': 'suite', 'id': suite['id']}
    peer.call('suite', 'run', args)
    case.started()
    _, cancelled = case.cancel_outcome(peer)
    assert cancelled['status'] == 'cancelled'
    _, body = peer.outcome('suite')
    result = body['result']['items'][0]
    assert result['status'] == 'cancelled' and result['not_executed']
    assert not case.rows('second')


@pytest.mark.parametrize('boundary', ['before-spawn', 'after-first'])
def test_cancel_at_real_pre_spawn_or_between_steps_boundary(case, boundary):
    import json
    from .support import wait_for
    case.child(marker='second', name='second')
    suite = case.scene.suites['suites'][0]
    suite['commands'] = [case.scene.selection(), case.scene.selection('second')]
    case.scene.save()
    peer = case.peer(observer=boundary); peer.initialize()
    args = case.arguments()
    if boundary == 'after-first':
        args['selection'] = {'kind': 'suite', 'id': suite['id']}
    peer.call('scheduled', 'run', args)
    witness = case.root / 'observer.jsonl'
    wait_for(witness.exists, 'actual observer boundary')
    rows = [json.loads(line) for line in witness.read_text().splitlines()]
    expected = 'runner-before-spawn' if boundary == 'before-spawn' else 'after-first-owned-step'
    assert [row['boundary'] for row in rows] == [expected]
    assert rows[0]['pid'] == peer.process.pid
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    identity = preview['target']['invocation_id']
    peer.call('cancel', 'cancel', case.cancel(identity=identity))
    signal_path = case.scene.state / case.policy['invocation']['directory'] / identity / case.policy['invocation']['cancel_file']
    wait_for(signal_path.exists, 'actual cancellation producer request')
    (case.root / 'observer.release').write_text('continue unchanged original owner\n')
    _, cancelled = peer.outcome('cancel')
    assert cancelled['status'] == 'cancelled'
    _, body = peer.outcome('scheduled')
    assert body['result']['status'] == 'cancelled'
    assert not case.rows('second')
    if boundary == 'before-spawn':
        assert not case.rows()
        assert cancelled['termination']['graceful_requested'] is False
        assert cancelled['termination']['force_requested'] is False
    else:
        assert [row['event'] for row in case.rows()] == ['STARTED', 'EXIT']
        assert case.record(body)['actual_exit_code'] == 0
