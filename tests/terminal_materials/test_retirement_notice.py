"""Receipt display follows actual material settlement, never Task status alone."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request
from poise.common import PoiseError
from .helpers import agree, arrange, declaration, finish


def checked_case(project):
    tools, root, source, commit = arrange(project, with_checks=True)
    receipt = deepcopy(tools.runtime.evidence_commands.list_for('T1')[0])
    assert Path(receipt['stdout']).is_file()
    return tools, root, receipt, commit


def shown(tools, receipt):
    return tools.runtime.result_views.present_result(
        {'checks': [receipt]}, tools.runtime.retired_receipt,
        tools.runtime.task_queries.receipt_task)


def test_completed_without_agreement_keeps_live_log_presentation(project):
    tools, root, receipt, _ = checked_case(project)
    before = Path(receipt['stdout']).read_bytes()
    assert tools.invoke(request('accept', {}))['status'] == 'completed'
    assert tools.runtime.delivery_tools.query('T1')['status'] == 'delivery_unagreed'
    full, metadata, views = shown(tools, receipt)
    assert root.is_dir() and Path(receipt['stdout']).read_bytes() == before
    assert 'notice' not in full and 'notice' not in metadata
    assert views[0].get('availability') != 'retired'
    assert 'presentation_error' not in views[0]
    assert full['checks'] == [receipt]
    assert views[0]['actual_exit_code'] == receipt['actual_exit_code']
    assert views[0]['passed'] == receipt['passed']


def test_completed_pending_integration_does_not_claim_removal(project):
    tools, root, receipt, commit = checked_case(project)
    agree(tools, declaration([{'id': 'source', 'kind': 'git',
        'repository': str(project['app']), 'target_ref': 'refs/heads/main',
        'commit': commit}]))
    done = tools.invoke(request('accept', {}))
    assert done['delivery']['status'] == 'delivery_blocked'
    assert done['delivery']['reason'] == 'integration_required'
    assert root.is_dir() and Path(receipt['stdout']).is_file()
    full, metadata, views = shown(tools, receipt)
    assert 'notice' not in full and 'notice' not in metadata
    assert views[0].get('availability') != 'retired'


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_actual_retirement_preserves_receipt_without_reading_removed_logs(project, status):
    tools, root, receipt, commit = checked_case(project)
    agree(tools, {'disposition': 'no_result', 'outputs': []})
    finish(project, tools, commit, status)
    assert not root.exists() and not Path(receipt['stdout']).exists()
    assert tools.runtime.delivery_tools.query('T1')['status'] == 'delivery_complete'
    full, metadata, views = shown(tools, receipt)
    expected = json.loads((Path(__file__).parent / 'fixtures/retirement_notice_expected.json').read_text())
    expected['metadata']['historical_receipts'][0]['checks'][0]['id'] = receipt['id']
    expected['views'][0]['id'] = receipt['id']
    assert metadata == expected['metadata']
    assert full == {'checks': [receipt], **expected['metadata']}
    assert views == expected['views']



def test_interrupted_retirement_does_not_claim_logs_already_removed(project, monkeypatch):
    tools, root, receipt, commit = checked_case(project)
    agree(tools, {'disposition': 'no_result', 'outputs': []})

    def interrupted(state):
        raise PoiseError('Test interruption before filesystem removal')

    monkeypatch.setattr(tools.runtime.delivery_effects, 'remove_owned_root', interrupted)
    with pytest.raises(PoiseError, match='Test interruption before filesystem removal'):
        finish(project, tools, commit, 'cancelled')
    state = tools.runtime.delivery_tools.state('T1')
    assert state.data['retirement_started'] is True
    assert state.data['status'] == 'delivery_partial'
    assert root.is_dir() and Path(receipt['stdout']).is_file()
    full, metadata, views = shown(tools, receipt)
    assert 'notice' not in full and 'notice' not in metadata
    assert views[0].get('availability') != 'retired'


def test_delivery_lookup_failure_is_visible_without_false_removal(project, monkeypatch):
    tools, root, receipt, _ = checked_case(project)
    tools.invoke(request('accept', {}))

    def unavailable(task_id):
        raise PoiseError('Test delivery owner unavailable')

    monkeypatch.setattr(tools.runtime.delivery_tools, 'state', unavailable)
    full, metadata, views = shown(tools, receipt)
    assert root.is_dir()
    assert 'notice' not in full
    assert metadata['presentation_errors'] == [{
        'id': receipt['id'], 'presentation_error': 'Test delivery owner unavailable'}]
