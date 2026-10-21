"""Truthful refusal, persistence and controlled taskless lifetime boundaries."""
import json
from pathlib import Path

import pytest

from tests.runtime_services.tool_results_support import Scenario, literal, write_json


@pytest.mark.parametrize('case', ['missing', 'unreadable', 'malformed'])
def test_unavailable_or_unusable_source(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    s.capture()
    request = s.request('view.template.json')
    source = s.root / 'unavailable.json'
    if case == 'unreadable': source.mkdir()
    if case == 'malformed': source.write_bytes(b'not valid JSON\n')
    request['response_path'] = str(source)
    before = s.snapshot()
    code = {'missing': 'source_missing', 'unreadable': 'source_unreadable',
            'malformed': 'source_invalid'}[case]
    s.refusal(request, code, 'provide:source' if case == 'missing' else 'restore:source')
    assert s.snapshot() == before


@pytest.mark.parametrize('case', ['missing-policy', 'invalid-known', 'unmapped-input', 'address-too-small'])
def test_policy_and_budget_refusals(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    s.bash_producer()
    request = s.request()
    if case == 'missing-policy':
        s.policy_path.unlink()
        code, action = 'policy_unavailable', 'provide:policy'
    elif case == 'invalid-known':
        s.policy['inspection']['max_lines'] = 0; s.save_policy()
        code, action = 'invalid_policy', 'correct:policy'
    elif case == 'unmapped-input':
        request['tool'] = {'kind': 'unconfigured-host', 'name': 'other'}
        code, action = 'unmapped_input', 'configure:input'
    else:
        s.policy['display']['output_chars'] = 1
        s.policy['display']['output_bytes'] = 1; s.save_policy()
        code, action = 'budget_too_small', 'increase:budget'
    before = s.snapshot()
    s.refusal(request, code, action)
    assert s.snapshot() == before
    assert s.out.read_bytes().startswith(b'FIRST_STDOUT\n')
    assert s.producer_count.read_text() == '1'


def test_persistence_failure_keeps_original_outcome(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.bash_producer(exit_code=7)
    blocker = tmp_path / 'blocked-parent'
    blocker.write_bytes(b'not a directory')
    blocked = blocker / 'results'
    s.policy['storage']['root'] = str(blocked)
    s.save_policy()
    placement = json.loads(s.placement.read_bytes())
    placement['roots'][0]['path'] = str(blocked)
    write_json(s.placement, placement)
    request = s.request()
    value = s.refusal(request, 'storage_write', 'retry:capture')
    assert value['exit_code'] == 7
    assert s.producer_count.read_text() == '1'
    assert s.out.read_bytes().startswith(b'FIRST_STDOUT\n')
    assert blocker.read_bytes() == b'not a directory'
    assert s.snapshot() == {}
