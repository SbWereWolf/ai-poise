"""Optional display failure must not erase actual owner termination facts."""
from copy import deepcopy

import pytest

from batch.helpers import verify
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
import poise.execution as execution
from runtime_services.test_check_termination_proof import assert_proof, state
from runtime_services.test_durable_check_attempts import candidate
from runtime_services.test_task_restart import restart
from runtime_services.test_uncertain_check_transfer import _handoff


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_preview_read_failure_preserves_stopped_run_for_transfer_and_archive(
    project, monkeypatch, stream,
):
    project['task']['planning'] = {
        'schema': 'task-planning-1', 'template': None,
        'restart_revision_policy': {'reviewer': ['process'], 'user': ['process']},
    }
    tools, payload, calls = candidate(project, monkeypatch)
    preview = execution.preview
    kill_group = tools.runtime.check_runner._kill_group
    observed_exits = []
    preview_failures = []
    attempted_receipts = []

    def observe_cleanup(child):
        answer = kill_group(child)
        observed_exits.append(child.poll())
        return answer

    def failed_preview(path, chars):
        if path.name == f'{stream}.txt':
            preview_failures.append(path)
            raise OSError('optional display read unavailable')
        return preview(path, chars)

    def lose_receipt(task_id, actor, stage, iteration, receipt):
        attempted_receipts.append(deepcopy(receipt))
        raise OSError('later receipt persistence unavailable')

    monkeypatch.setattr(tools.runtime.check_runner, '_kill_group', observe_cleanup)
    monkeypatch.setattr(execution, 'preview', failed_preview)
    monkeypatch.setattr(tools.runtime.evidence_commands, 'record_receipt', lose_receipt)
    with pytest.raises(OSError, match='later receipt persistence unavailable'):
        verify(tools, deepcopy(payload))
    pending = deepcopy(tools.runtime.current_task()['pending'])
    assert observed_exits and all(code == 0 for code in observed_exits)
    assert len(preview_failures) == 1
    assert_proof(pending)
    receipt, = attempted_receipts
    assert receipt['preview_errors'] == {stream: 'OSError'}
    assert receipt[f'{stream}_preview'] == ''
    assert receipt['actual_exit_code'] == 0
    assert receipt['capture_complete'] is True
    assert tools.runtime.evidence_commands.list_for('T1') == []
    before = state(tools)
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(tools, deepcopy(payload))
    assert state(tools) == before
    assert calls == [pending['runs'][0]['run_id']]
    assert _handoff(tools, pending, request_id=f'preview-{stream}-transfer')['status'] == 'handed_off'
    reviewer = WorkTools(WorkPoise(project['config_path'], f'preview-{stream}-reviewer'))
    reviewer.runtime.bootstrap({'id': 'T1'})
    current = reviewer.runtime.current_task()
    answer = restart(reviewer, 'T1', current['version'], f'preview-{stream}-archive',
                     authorization={'role': 'reviewer', 'decision':
                                    'Archive proved stopped run without replay or fabricated receipt.'})
    assert answer['status'] == 'newborn'
    after = reviewer.runtime.task_queries.record('T1')
    assert after['restart_history'][-1]['abandoned_check_attempt'] == pending
    assert after['worktree'] == before['task']['worktree']
    assert after['history'][:len(before['task']['history'])] == before['task']['history']
    assert reviewer.runtime.evidence_commands.list_for('T1') == []
    assert calls == [pending['runs'][0]['run_id']]
