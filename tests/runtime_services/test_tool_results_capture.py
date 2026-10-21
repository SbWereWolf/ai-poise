"""Public complete-result producers and actual external machine consumers."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.runtime_services.tool_results_support import FIXTURES, Scenario, literal, write_json


@pytest.mark.parametrize('size,exit_code', [('short', 0), ('large', 7)],
                         ids=['short-success', 'large-failure'])
def test_bash_full_capture_and_machine_consumer(project, tmp_path, size, exit_code):
    s = Scenario(project, tmp_path)
    produced = s.bash_producer(size, exit_code)
    request = s.request(exit_code=exit_code)
    value, saved = s.capture(request, 'machine')
    assert value['response'] == request['response']
    assert Path(saved['streams']['stdout']['path']).read_bytes() == produced.stdout
    assert Path(saved['streams']['stderr']['path']).read_bytes() == produced.stderr
    assert produced.stdout.startswith(b'FIRST_STDOUT\n')
    assert produced.stderr.startswith(b'FIRST_STDERR\n')
    shown = s.success(s.request('view.template.json'))
    assert shown['exit_code'] == exit_code
    assert s.producer_count.read_text() == '1'


@pytest.mark.parametrize('size', ['short', 'large'], ids=['small', 'large'])
def test_mcp_full_envelope_and_machine_consumer(project, tmp_path, size):
    s = Scenario(project, tmp_path)
    counter = tmp_path / 'mcp-count.txt'
    cp = subprocess.run([sys.executable, '-B', str(FIXTURES / 'producer.py'), '--kind', 'mcp',
                         '--size', size, '--counter', str(counter), '--exit-code', '0'],
                        capture_output=True, check=False)
    assert cp.returncode == 0 and cp.stderr == b''
    expected = literal('mcp-large-response.json' if size == 'large' else 'mcp-response.json')
    assert json.loads(cp.stdout) == expected
    request = s.request(tool={'kind': 'mcp', 'name': 'mcp__fixture__query'}, response=json.loads(cp.stdout),
                        streams={'stdout': None, 'stderr': None}, exit_code=None)
    value, saved = s.capture(request, 'machine')
    assert value['response'] == saved['response'] == expected
    assert saved['response']['result']['content'][2]['data'] == 'AAEC'
    assert saved['response']['result']['structuredContent']['MACHINE_ONLY_NESTED'] == [0, False, None, {'value': 'Ж🙂'}]
    assert saved['response']['extra_transport'] == 'preserved'
    assert counter.read_text() == '1'
    s.success(s.request('view.template.json'))
    assert counter.read_text() == '1'


@pytest.mark.parametrize('case', ['empty', 'unicode'])
def test_generic_json_and_empty_streams(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    s.out.write_bytes(b'' if case == 'empty' else 'Ж🙂\r\n'.encode())
    expected = {} if case == 'empty' else literal('generic-response.json')
    request = s.request(tool={'kind': 'generic', 'name': 'fixture'}, response=expected,
                        streams={'stdout': str(s.out), 'stderr': None})
    value, saved = s.capture(request, 'machine')
    assert saved['response'] == expected and saved['streams']['stderr'] is None
    assert saved['streams']['stdout']['bytes'] == (0 if case == 'empty' else 8)
    assert value['response_path'] == str(s.response_path)


@pytest.mark.parametrize('state,reason,exit_code', [
    ('complete', None, 0), ('partial', 'upstream capture limit', 7), ('unknown', None, None)],
    ids=['complete', 'partial', 'unknown'])
def test_complete_partial_unknown_are_not_inferred(project, tmp_path, state, reason, exit_code):
    s = Scenario(project, tmp_path)
    capture = {'state': state, 'reason': reason}
    req = s.request(capture=capture, exit_code=exit_code)
    value, saved = s.capture(req, 'machine')
    before = s.snapshot()
    for mode in ['brief', 'expanded', 'full']:
        v = s.success(s.request('view.template.json', view=mode))
        assert v['capture'] == capture and v['exit_code'] == exit_code
    assert value['capture'] == saved['capture'] == capture
    assert s.snapshot() == before


@pytest.mark.parametrize('kind', ['bash', 'mcp', 'generic'])
def test_mcp_and_bash_machine_outputs_ignore_agent_budget(project, tmp_path, kind):
    s = Scenario(project, tmp_path)
    req = s.request(tool={'kind': kind, 'name': 'fixture'}, response=literal('generic-response.json'))
    original, _ = s.capture(req, 'machine')
    before = s.snapshot()
    s.policy['display']['output_chars'] = 1
    s.policy['display']['output_bytes'] = 1
    s.save_policy()
    repeated, _ = s.capture(req, 'machine')
    assert repeated == original
    assert s.snapshot() == before
    s.refusal(s.request('view.template.json'), 'budget_too_small', 'increase:budget')
    assert s.snapshot() == before


def test_taskless_placement_and_identity_replay(project, tmp_path):
    s = Scenario(project, tmp_path)
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    assert not database.exists()
    database.mkdir(parents=True)
    sentinel = tmp_path / 'foreign-sentinel.txt'
    sentinel.write_bytes(b'foreign-owned-bytes\x00')
    req = s.request()
    first, _ = s.capture(req)
    before = s.snapshot()
    second, _ = s.capture(req)
    assert first == second and s.snapshot() == before
    conflicting = deepcopy(req); conflicting['response']['output'] = 'different result'
    s.refusal(conflicting, 'request_conflict', 'choose:new_key')
    assert s.snapshot() == before
    assert sentinel.read_bytes() == b'foreign-owned-bytes\x00'
    assert database.is_dir() and list(database.iterdir()) == []
    assert not any(p.name in ('standalone', 'sprints') for p in s.root.rglob('*'))


@pytest.mark.parametrize('mode,size', [('audit', 'short'), ('audit', 'large'),
                                      ('enforce', 'short'), ('enforce', 'large')],
                         ids=['audit-small', 'audit-large', 'enforce-small', 'enforce-large'])
def test_controlled_bash_hook_uses_common_owner(project, tmp_path, mode, size):
    s = Scenario(project, tmp_path)
    s.policy['hook']['mode'] = mode; s.save_policy()
    s.bash_producer(size)
    response = s.request()['response']
    response['output'] = (FIXTURES / (size + '.stdout.log')).read_text()
    ordinary = s.request(request_id='hook-1', response=response)
    plain, saved = s.capture(ordinary, 'machine')
    before = s.snapshot()
    hook = s.request('hook.template.json')
    hook['event']['tool_response'] = response
    result = s.success(hook)
    # Both actual public routes must normalize to the same saved owned result.
    assert result['response_path'] == plain['response_path']
    assert s.snapshot() == before
    assert json.loads(s.response_path.read_bytes())['response'] == response
    assert Path(saved['streams']['stdout']['path']).read_bytes() == s.out.read_bytes()
    if mode == 'audit':
        assert result['action'] == 'would_replace'
    else:
        assert result['action'] == 'enforced'
        assert result['protocol']['continue'] is False
        assert isinstance(result['protocol']['stopReason'], str) and result['protocol']['stopReason']
        assert result['protocol']['hookSpecificOutput']['hookEventName'] == 'PostToolUse'
        assert str(s.response_path) in result['protocol']['hookSpecificOutput']['additionalContext']
    assert s.producer_count.read_text() == '1'
