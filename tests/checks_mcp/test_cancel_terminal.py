"""A late request preserves the actual outcome of an already returned step."""
import json
from pathlib import Path

from .support import wait_for


def test_late_cancel_preserves_completed_timeout(case):
    case.child(mode='hold')
    case.scene.profile()['execution_timeout_seconds'] = 1
    case.scene.save()
    peer = case.peer(observer='after-first'); peer.initialize()
    peer.call('run', 'run', case.arguments(fresh=True))
    started = case.started()
    witness = case.root / 'observer.jsonl'
    wait_for(witness.exists, 'actual returned terminal step')
    rows = [json.loads(line) for line in witness.read_text().splitlines()]
    assert rows == [{'boundary': 'after-first-owned-step', 'pid': peer.process.pid}]
    assert started['pid'] != peer.process.pid
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    assert preview['status'] == 'planned'
    identity = preview['target']['invocation_id']
    assert len(preview['target']['run_ids']) == 1
    assert preview['termination'] == {
        'graceful_requested': False, 'force_requested': True,
        'process_group_stopped': True, 'child_reaped': True, 'capture_complete': True}
    peer.call('cancel', 'cancel', case.cancel(identity=identity))
    signal_path = (case.scene.state / case.policy['invocation']['directory'] / identity
                   / case.policy['invocation']['cancel_file'])
    wait_for(signal_path.exists, 'actual late cancellation producer request')
    (case.root / 'observer.release').write_text('continue unchanged original owner\n')
    _, body = peer.outcome('run')
    record = case.record(body)
    assert body['invocation']['id'] == identity
    assert record['run_id'] == preview['target']['run_ids'][0]
    assert record['status'] == 'timed_out'
    assert record['actual_exit_code'] == -9
    assert record['termination']['timed_out'] is True
    assert record['termination']['cancelled'] is False
    assert record['termination']['capture_complete'] is True
    assert record['termination']['child_reaped'] is True
    assert record['termination']['process_group_stopped'] is True
    assert [row['event'] for row in case.rows()] == ['STARTED']
    case.assert_streams(record)
    _, cancelled = peer.outcome('cancel')
    assert cancelled['target'] == preview['target']
    assert cancelled['references']['response_path'] == body['result']['response_path']
    assert cancelled['references']['record_paths'] == [record['record_path']]
    assert cancelled['references']['stdout_paths'] == [record['stdout']['path']]
    assert cancelled['references']['stderr_paths'] == [record['stderr']['path']]
    assert Path(cancelled['references']['response_path']).is_file()
    assert cancelled['termination'] == preview['termination']
    assert cancelled['status'] == 'complete'
