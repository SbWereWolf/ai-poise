"""Public verification reuse must preserve lawful failed-check recovery."""
from copy import deepcopy
import sys

import pytest

from batch.helpers import result, verify
from runtime_services.test_failed_check_rework import _rework, _scenario, _show


@pytest.mark.parametrize('mixed_batch', [False, True])
def test_reused_failed_checks_bind_new_submission_without_rewriting_receipts(project, mixed_batch):
    tools, context = _scenario(
        project,
        extra_command=[sys.executable, '-c', 'raise SystemExit(0)'] if mixed_batch else None,
    )
    original = verify(tools, result(context, 'Original submitted report.'))
    assert original['status'] == 'checks_failed'
    immutable_receipts = deepcopy(original['checks'])
    old_digest = immutable_receipts[0]['submission_digest']
    before = _show(tools)

    repeated = verify(tools, result(context, 'Updated report with newly published diagnostic evidence.'))

    assert repeated['status'] == 'checks_failed'
    assert repeated['checks'] == immutable_receipts
    assert _show(tools)['attempts'] == before['attempts'] == 1
    assert _show(tools)['submission_count'] == before['submission_count'] + 1
    assert _show(tools)['evidence_count'] == before['evidence_count']
    assert tools.runtime.current_task()['pending'] is None
    recovered = _rework(tools, 'implementation')
    assert recovered['status'] == 'active'
    assert recovered['stage'] == 'implementation'
    assert recovered['iteration'] == 2
    assert recovered['worktree'] == context['worktree']
    assert repeated['checks'] == immutable_receipts
    assert repeated['checks'][0]['submission_digest'] == old_digest
