"""Lost output and cross-process cancellation use real preserved owner facts."""
from copy import deepcopy
import json
from pathlib import Path

from tests.named_checks.support import write_json


def results_for_subject(case):
    root = case.scene.state / case.scene.policy['storage']['runs_directory']
    values = []
    for path in root.rglob('result.json'):
        value = json.loads(path.read_text())
        if any('checks_mcp/fixtures/child.py' in ' '.join(step['record']['argv'])
               for step in value['steps']):
            values.append(value)
    return values


def test_separate_cli_process_receives_cancel_from_mcp(case):
    case.child(mode='term')
    peer = case.peer(); peer.initialize()
    cli = case.spawn_cli()
    child = case.started()
    assert cli.pid != peer.process.pid != child['pid']
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    assert preview['status'] == 'planned' and preview['target']['run_ids']
    _, cancelled = case.cancel_outcome(peer, identity=preview['target']['invocation_id'])
    assert cancelled['status'] == 'cancelled'
    assert cli.wait(timeout=20) == 1
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']
    results, = results_for_subject(case)
    assert results['steps'][0]['record']['actual_exit_code'] == 17
    assert results['steps'][0]['record']['status'] == 'cancelled'


def test_broken_terminal_reply_preserves_readable_result_without_rerun(case):
    case.child(mode='hold')
    peer = case.peer(); peer.initialize()
    peer.call('lost', 'run', case.arguments())
    case.started()
    peer.process.stdout.close()  # Actual receiver failure, after a real STARTED witness.
    case.release()
    from .support import wait_for
    wait_for(lambda: results_for_subject(case), 'result saved after broken terminal reply')
    peer.process.stdin.close()
    peer.process.wait(timeout=20)
    results, = results_for_subject(case)
    before = deepcopy(case.rows())
    request = case.scene.request('result', items=[{'result_id': results['result_id']}])
    code, readback, _ = case.scene.invoke(request)
    assert code == 0 and readback['items'][0] == results
    assert case.rows() == before
    record = results['steps'][0]['record']
    assert record['actual_exit_code'] == 0 and record['termination']['capture_complete'] is True
    case.assert_streams(record)


def test_real_response_storage_failure_after_child_keeps_known_outcome(case):
    peer = case.peer(); peer.initialize()
    case.full_run(peer, 'healthy')
    policy = deepcopy(case.scene.policy)
    policy['storage']['response_directory'] = 'blocked-response-directory'
    sentinel = case.scene.state / 'blocked-response-directory'
    sentinel.write_bytes(b'INDEPENDENT_STORAGE_SENTINEL')
    changed = write_json(case.root / 'blocked-policy.json', policy)
    binding = {'config': str(case.scene.config), 'checkout': str(case.scene.checkout), 'policy': str(changed)}
    envelope, body = case.full_run(peer, 'save-failed', case.arguments(binding=binding))
    assert len([row for row in case.rows() if row['event'] == 'STARTED']) == 2, 'SAVE_FAILURE_SUBJECT_NOT_REACHED'
    assert envelope['isError'] is True and body['result']['status'] == 'unconfirmed'
    assert body['result']['error']['code'] == 'result_save_failed'
    assert body['result']['response_path'] is None
    assert case.record(body)['actual_exit_code'] == 0
    case.assert_streams(case.record(body))
    assert sentinel.read_bytes() == b'INDEPENDENT_STORAGE_SENTINEL'


def test_real_missing_capture_is_not_reported_as_verified_success(case):
    case.child(mode='hold')
    peer = case.peer(); peer.initialize()
    peer.call('capture', 'run', case.arguments())
    case.started()
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    paths = preview['references']['stdout_paths']
    assert len(paths) == 1
    path = Path(paths[0])
    from .support import wait_for
    wait_for(lambda: path.exists() and path.read_bytes() == case.stdout_file.read_bytes(), 'real-capture-injection')
    path.unlink()
    assert not path.exists(), 'CAPTURE_INJECTION_NOT_REACHED'
    case.release()
    envelope, body = peer.outcome('capture')
    assert envelope['isError'] is True
    record = case.record(body)
    assert record['actual_exit_code'] == 0 and record['verified_success'] is False
    assert record['termination']['capture_complete'] is False
    assert record['error']['code'] == 'capture_failed'
    assert Path(record['stderr']['path']).read_bytes() == case.stderr_file.read_bytes()


def test_eof_cleans_up_owned_running_check_and_saves_truth(case):
    case.child(mode='term')
    peer = case.peer(); peer.initialize()
    peer.call('running', 'run', case.arguments())
    case.started()
    peer.process.stdin.close()
    peer.process.wait(timeout=20)
    saved, = results_for_subject(case)
    record = saved['steps'][0]['record']
    assert record['status'] == 'cancelled' and record['actual_exit_code'] == 17
    assert record['termination']['child_reaped'] is True
    assert record['termination']['capture_complete'] is True
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']


def test_protocol_cancel_notification_reaches_exact_request(case):
    case.child(mode='term')
    peer = case.peer(); peer.initialize()
    peer.call('real-request', 'run', case.arguments())
    case.started()
    from .support import literal
    peer.send(literal('cancel-notification.json'))
    _, body = peer.outcome('real-request')
    assert case.record(body)['status'] == 'cancelled'
    assert case.record(body)['actual_exit_code'] == 17
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']


def test_unknown_metadata_is_preserved_without_signal_or_fake_completion(case):
    case.child(mode='term')
    peer = case.peer(); peer.initialize()
    peer.call('held', 'run', case.arguments())
    case.started()
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    identity = preview['target']['invocation_id']
    folder = case.scene.state / case.policy['invocation']['directory'] / identity
    path = folder / case.policy['invocation']['record_file']
    saved = path.read_bytes()
    assert json.loads(saved), 'UNKNOWN_INJECTION_HEALTHY_RECORD_NOT_REACHED'
    damaged = b'{invalid-independent-test-data'
    path.write_bytes(damaged)
    try:
        _, unknown = case.cancel_outcome(peer, 'unknown', identity=identity)
        assert unknown['status'] == 'unknown'
        assert unknown['termination']['graceful_requested'] is False
        assert unknown['termination']['force_requested'] is False
        assert path.read_bytes() == damaged, 'UNKNOWN_INPUT_WAS_REPLACED'
        assert not (folder / case.policy['invocation']['cancel_file']).exists()
        assert not [row for row in case.rows() if row['event'] == 'TERM']
    finally:
        path.write_bytes(saved)  # Restore only this isolated deliberate test injection.
    _, stopped = case.cancel_outcome(peer, 'healthy-control', identity=identity)
    assert stopped['status'] == 'cancelled'
    _, completed = peer.outcome('held')
    assert case.record(completed)['actual_exit_code'] == 17
