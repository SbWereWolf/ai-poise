"""Replay genuine completed command receipts under the original v1 descriptor."""

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from batch.helpers import result, verify
from poise.common import PoiseError
from runtime_services.test_check_termination_proof import patch_pending, state
from runtime_services.test_failed_check_rework import _scenario


TERMINALS = {
    'timeout': 'import time; time.sleep(10)',
    'signal': 'import os,signal; os.kill(os.getpid(), signal.SIGTERM)',
    'failure': 'print("known-failure"); raise SystemExit(7)',
}


def _captured_v1(project, monkeypatch, terminal):
    project['cfg']['runtime_services']['check_runner'].update(
        initial_seconds=2, progress_gap_seconds=0.1, poll_seconds=0.02)
    tools, context = _scenario(project,
        command=[sys.executable, '-S', '-c', 'print("green-prefix")'],
        extra_command=[sys.executable, '-S', '-c',
                       'print("expected-red"); raise SystemExit(1)'],
        expected_red=True,
        third_command=[sys.executable, '-S', '-c', TERMINALS[terminal]])
    payload = result(context, 'Finalize authentic historical v1 complete receipts.')
    calls = []
    original_run = tools.runtime.check_runner.run
    original_record = tools.runtime.evidence_commands.record_receipt
    recorded = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original_run(*args, **kwargs)

    def lose_final_ack(*args, **kwargs):
        receipt = original_record(*args, **kwargs)
        recorded.append(True)
        if len(recorded) == 3:
            raise OSError('all terminal receipts saved; final acknowledgement lost')
        return receipt

    monkeypatch.setattr(tools.runtime.check_runner, 'run', counted)
    monkeypatch.setattr(tools.runtime.evidence_commands, 'record_receipt', lose_final_ack)
    with pytest.raises(OSError, match='all terminal receipts saved'):
        verify(tools, deepcopy(payload))
    monkeypatch.setattr(tools.runtime.evidence_commands, 'record_receipt', original_record)
    pending = deepcopy(tools.runtime.current_task()['pending'])
    receipts = deepcopy(tools.runtime.evidence_commands.list_for('T1'))
    assert len(receipts) == len(pending['runs']) == 3
    assert calls == [run['run_id'] for run in pending['runs']]
    assert all(run['started'] is True for run in pending['runs'])
    assert tools.runtime.check_runner.active_ids() == ()

    # Disposable historical-protocol arrangement only. Preserve every original
    # identity and all immutable receipt bytes. No synthetic receipt or witness.
    v1 = {key: pending[key] for key in (
        'kind', 'attempt_id', 'task_id', 'task_version', 'actor', 'stage',
        'iteration', 'submission_digest', 'verified_tree', 'execution_key')}
    v1['version'] = 1
    v1['runs'] = [{key: run[key] for key in ('run_id', 'method_id', 'started')}
                  for run in pending['runs']]
    patch_pending(tools, v1)
    assert tools.runtime.current_task()['pending'] == v1
    assert (v1['stage'], v1['iteration']) == (context['stage'], context['iteration'])
    assert all('termination' not in run for run in v1['runs'])
    assert tools.runtime.evidence_commands.list_for('T1') == receipts
    files = {}
    for run, receipt in zip(v1['runs'], receipts, strict=True):
        for field, expected in {
            'id': run['run_id'], 'method': run['method_id'],
            'attempt_id': v1['attempt_id'], 'tree': v1['verified_tree'],
            'execution_key': v1['execution_key'],
            'submission_digest': v1['submission_digest'],
        }.items():
            assert receipt[field] == expected
        assert receipt['source_unchanged'] is True
        assert receipt['capture_complete'] is True
        for stream in ('stdout', 'stderr'):
            path = Path(receipt[stream])
            files[path] = path.read_bytes()
            assert hashlib.sha256(files[path]).hexdigest() == receipt[stream + '_digest']
    assert Path(receipts[0]['stdout']).read_text() == 'green-prefix\n'
    assert Path(receipts[1]['stdout']).read_text() == 'expected-red\n'
    assert [receipt['passed'] for receipt in receipts] == [True, True, False]
    assert receipts[1]['expected_exit_code'] == receipts[1]['actual_exit_code'] == 1
    assert receipts[2]['actual_exit_code'] == {'timeout': -9, 'signal': -15, 'failure': 7}[terminal]
    assert receipts[2]['timed_out'] is (terminal == 'timeout')
    return tools, payload, v1, receipts, files, calls


@pytest.mark.parametrize('terminal', ['timeout', 'signal', 'failure'])
def test_all_present_v1_finalizes_known_failure_without_reexecution(project, monkeypatch, terminal):
    tools, payload, v1, receipts, files, calls = _captured_v1(project, monkeypatch, terminal)
    before = state(tools)
    first = verify(tools, deepcopy(payload))
    assert first['status'] == 'checks_failed'
    assert first['checks'] == receipts
    after = state(tools)
    assert after['task']['pending'] is None
    assert after['task']['attempts'] == before['task']['attempts'] == 1
    assert after['task']['history'][:len(before['task']['history'])] == before['task']['history']
    assert (after['claims'], after['registry'], after['head'], after['status']) == (
        before['claims'], before['registry'], before['head'], before['status'])
    assert not any('restart' in event['event'] for event in
                   after['task']['history'][len(before['task']['history']):])
    assert tools.runtime.evidence_commands.list_for('T1') == receipts
    replay = verify(tools, deepcopy(payload))
    assert replay['status'] == 'checks_failed'
    assert replay['checks'] == receipts
    assert state(tools) == after
    assert calls == [run['run_id'] for run in v1['runs']]
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.parametrize('damage', [
    'missing-output', 'changed-output', 'missing-receipt', 'run-identity', 'protocol'])
def test_v1_terminal_damage_refuses_replay_without_effect(project, monkeypatch, damage):
    tools, payload, v1, receipts, files, calls = _captured_v1(project, monkeypatch, 'timeout')
    if damage == 'missing-output':
        Path(receipts[0]['stdout']).unlink()
    elif damage == 'changed-output':
        Path(receipts[1]['stderr']).write_bytes(b'corrupt capture')
    elif damage == 'missing-receipt':
        # Corrupt only the disposable fixture's persisted evidence set.
        with tools.runtime.store.transaction() as database:
            database.execute('DELETE FROM evidence WHERE id=?', (receipts[2]['id'],))
    elif damage == 'run-identity':
        v1['runs'][0]['run_id'] = str(uuid4())
        patch_pending(tools, v1)
    else:
        v1['version'] = 999
        patch_pending(tools, v1)
    before = state(tools)
    bytes_before = {path: path.read_bytes() if path.exists() else None for path in files}
    with pytest.raises(PoiseError, match='Unknown|unknown|unsupported|context'):
        verify(tools, deepcopy(payload))
    assert state(tools) == before
    assert tools.runtime.evidence_commands.list_for('T1') == before['receipts']
    assert calls == [receipt['id'] for receipt in receipts]
    assert {path: path.read_bytes() if path.exists() else None for path in files} == bytes_before
