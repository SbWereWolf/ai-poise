"""S/C: real publication and last-result consumers, no alternate store."""
from copy import copy
from itertools import islice
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex

import pytest

from tests.runtime_services.session_tool_results_support import (
    OutputCapabilityMissing, ParserCalls, SessionScenario, cases, prepare_owner,
    snapshot,
)
from tests.runtime_services.tool_results_support import FIXTURES, literal, write_json


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'mcp'])
def test_default_agent_brief_hides_saved_navigation_but_explicit_views_reuse_source(
        project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    request = scene.input(case)
    before_request = json.dumps(request, ensure_ascii=False)
    code, agent = scene.direct(request)
    assert code == 0 and agent['preview'] == cases()[case]['brief']
    assert agent['exit_code'] == cases()[case]['exit_code']
    full = scene.service('full')
    before = scene.snapshot()
    expanded = scene.service('expanded')
    assert expanded['text'] == cases()[case]['expanded']
    assert full['run_id'] == expanded['run_id'] == scene.service('full')['run_id']
    assert scene.snapshot() == before
    assert json.loads(Path(full['full_path']).read_bytes()) == request['response']
    assert json.dumps(request, ensure_ascii=False) == before_request
    assert not {'response_path', 'full_path', 'expanded_path', 'last_path'} & agent.keys(), (
        'DEFAULT_AGENT_NAVIGATION_LEAK', agent)
    assert str(scene.root) not in json.dumps(agent)


def test_pending_agent_brief_preserves_unknown_outcome_without_saved_navigation(project, tmp_path):
    from tests.runtime_services.test_tool_results_universal import request_for
    scene = SessionScenario(project, tmp_path)
    request = request_for(scene, 'pending')
    code, machine = scene.direct(request, 'machine')
    assert code == 0 and machine['response'] == request['event']['tool_response']
    before = scene.snapshot()
    code, agent = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'view',
                               'response_path': machine['response_path'], 'view': 'brief'})
    assert code == 0 and agent['capture'] == {'state': 'unknown', 'reason': 'native_process_pending'}
    assert agent['exit_code'] is None and 'pending' in agent['preview'].casefold()
    assert scene.snapshot() == before
    code, full = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'full'})
    assert code == 2 and full['error']['code'] == 'source_incomplete'
    assert not {'response_path', 'full_path', 'expanded_path', 'last_path'} & agent.keys(), (
        'DEFAULT_PENDING_NAVIGATION_LEAK', agent)


@pytest.mark.parametrize('chars,expected_code', [(500, 0), (140, 2)])
def test_default_agent_budget_counts_escaped_envelope_without_navigation(
        project, tmp_path, chars, expected_code):
    scene = SessionScenario(project, tmp_path)
    scene.policy['display'].update(output_chars=chars, output_bytes=chars * 2)
    scene.save_policy()
    request = scene.input()
    request['response']['output'] = 'RESULT: ' + '"\\' * 20 + '\n'
    code, agent = scene.direct(request)
    assert code == expected_code, 'DEFAULT_BRIEF_BUDGET_COUNTS_UNREQUESTED_NAVIGATION'
    wire = scene.calls[-1]['stdout']
    assert len(wire) <= chars and len(wire.encode()) <= chars * 2
    assert str(scene.root) not in wire and 'response_path' not in wire
    if code == 0:
        assert agent['preview'] == 'Bash: exit 0; ' + '"\\' * 20
    else:
        assert agent['status'] == 'rejected' and agent['error']['code'] == 'budget_too_small'


def test_two_tools_publish_distinct_uuid_files_and_last(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    first, _ = scene.stored(scene.input())
    first_id, first_full, first_expanded, last = scene.run_identity(first)
    first_bytes = (first_full.read_bytes(), first_expanded.read_bytes())
    second, _ = scene.stored(scene.input('mcp', key='second-real-tool'))
    second_id, second_full, second_expanded, second_last = scene.run_identity(second)
    assert first_id != second_id and first_full.parent != second_full.parent
    assert last == second_last and json.loads(last.read_bytes()) == {'uuid': second_id}
    assert first_bytes == (first_full.read_bytes(), first_expanded.read_bytes())
    assert json.loads(second_full.read_bytes()) == cases()['mcp']['response']
    assert second_expanded.read_text() == cases()['mcp']['expanded']
    assert scene.service('expanded')['run_id'] == second_id
    assert scene.service('full')['run_id'] == second_id


def test_same_root_sessions_keep_own_last_and_foreign_bytes(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    other = copy(scene)
    other.env = dict(scene.env)
    other.calls = []
    binding = write_json(tmp_path / 'second-direct-caller.json',
                         {'schema': 'poise-caller-binding-1', 'caller_id': 'session-output-b'})
    other.env['POISE_CALLER_BINDING'] = str(binding)
    sentinel = scene.root / 'foreign-sentinel'
    sentinel.write_bytes(b'foreign-owned\x00\r\n')
    one, _ = scene.stored(scene.input(key='same-tool-id'))
    two, _ = other.stored(other.input('mcp', key='same-tool-id'))
    a, af, _, al = scene.run_identity(one)
    b, bf, _, bl = other.run_identity(two)
    assert a != b and af != bf and al != bl
    assert scene.service('expanded')['text'] == cases()['bash-success']['expanded']
    assert other.service('expanded')['text'] == cases()['mcp']['expanded']
    before = snapshot(scene.root)
    # A foreign path cannot replace the caller's own last-result resolution.
    code, denied = scene.direct({'schema': 'poise-tool-result-request-1',
                                'operation': 'full', 'response_path': str(bf)})
    assert code == 2 and denied['status'] == 'rejected'
    assert snapshot(scene.root) == before
    assert sentinel.read_bytes() == b'foreign-owned\x00\r\n'
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    assert not database.exists(), 'F08_OUTPUT_CREATED_TASK_DATABASE'


def test_general_output_cli_needs_no_project_or_checkout(tmp_path):
    root = tmp_path / 'results'
    root.mkdir()
    tokens = {'@RESULTS@': str(root), '@PLACEMENT@': str(tmp_path / 'placement.json'),
              '@HARNESS@': str(tmp_path / 'harness-state')}
    def fill(value):
        if isinstance(value, str):
            for key, replacement in tokens.items():
                value = value.replace(key, replacement)
            return value
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value
    policy = fill(literal('policy.template.json'))
    policy.update(fill(literal('session-policy.additions.json')))
    policy_path = write_json(tmp_path / 'policy.json', policy)
    write_json(tmp_path / 'placement.json', fill(literal('placement.template.json')))
    binding = write_json(tmp_path / 'caller.json',
                         {'schema': 'poise-caller-binding-1', 'caller_id': 'projectless-output'})
    env = {k: v for k, v in os.environ.items() if k not in
           ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'POISE_CALLER_BINDING', 'POISE_CONFIG', 'POISE_SESSION')}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'),
               POISE_CALLER_BINDING=str(binding), PYTHONDONTWRITEBYTECODE='1')
    data = cases()['bash-success']
    request = fill(literal('capture.template.json'))
    request.update(tool=data['tool'], response=data['response'], exit_code=0,
                   streams={'stdout': None, 'stderr': None})
    argv = [sys.executable, '-B', '-m', 'poise', 'tool-result',
            '--policy', str(policy_path), '--audience', 'agent']
    cp = subprocess.run(argv, input=json.dumps(request).encode(), env=env,
                        cwd=tmp_path, capture_output=True, check=False)
    if (cp.returncode == 2 and not cp.stdout
            and b'the following arguments are required: --config, --checkout' in cp.stderr):
        raise OutputCapabilityMissing('F08_PROJECTLESS_OUTPUT_CLI_MISSING')
    assert cp.returncode == 0 and cp.stderr == b'', ('F08_PROJECTLESS_UNEXPECTED_ERROR', cp)
    value = json.loads(cp.stdout)
    assert value['preview'] == 'Bash: exit 0; 2 records'
    before = snapshot(root)
    full_request = {'schema': 'poise-tool-result-request-1', 'operation': 'full'}
    full = subprocess.run(argv, input=json.dumps(full_request).encode(), env=env,
                          cwd=tmp_path, capture_output=True, check=False)
    assert full.returncode == 0 and full.stderr == b''
    saved = json.loads(full.stdout)
    assert saved['status'] == 'full' and Path(saved['full_path']).is_file()
    assert json.loads(Path(saved['full_path']).read_bytes()) == request['response']
    last = list(root.rglob(policy['session']['last']))
    assert len(last) == 1 and json.loads(last[0].read_bytes()) == {'uuid': saved['run_id']}
    assert snapshot(root) == before, 'PROJECTLESS_FULL_READ_REPLAYED_OR_OVERWROTE_CAPTURE'
    assert not {'response_path', 'full_path', 'expanded_path', 'last_path'} & value.keys(), (
        'PROJECTLESS_DEFAULT_NAVIGATION_LEAK', value)
    assert not (tmp_path / '.git').exists()
    assert not (tmp_path / 'project.json').exists()


def test_complete_replay_reuses_uuid_new_call_executes_again(project, tmp_path):
    from poise.composition import tool_result_tools
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultPolicy
    scene = SessionScenario(project, tmp_path)
    argv = [sys.executable, '-B', str(FIXTURES / 'session_producer.py'),
            '--stdout', str(FIXTURES / 'session-native-large.stdout.txt'),
            '--counter', str(scene.producer_count), '--exit-code', '0']
    produced = subprocess.run(['bash', '-c', shlex.join(argv)], capture_output=True, check=False)
    assert produced.returncode == 0 and produced.stderr == b''
    request = scene.input()
    request['response']['output'] = produced.stdout.decode()
    prepare = prepare_owner()
    owner = tool_result_tools(read_project_checkout(scene.config, scene.checkout),
                              ToolResultPolicy.parse(scene.policy).data, scene.env)
    with ParserCalls(prepare) as calls:
        first = owner.execute(request, 'machine')
        first_id, _, _, _ = scene.run_identity(first)
        before = scene.snapshot()
        same = owner.execute(request, 'machine')
        assert scene.run_identity(same)[0] == first_id
        assert scene.snapshot() == before
        assert scene.producer_count.read_text() == '1'
        assert len(calls.inputs) == 1, 'F08_COMPLETED_REPLAY_REPARSED'
        second = subprocess.run(['bash', '-c', shlex.join(argv)], capture_output=True, check=False)
        assert second.returncode == 0 and second.stdout == produced.stdout
        request['request_id'] = 'intentional-new-tool-call'
        new = owner.execute(request, 'machine')
        assert scene.run_identity(new)[0] != first_id
        assert scene.producer_count.read_text() == '2'
        assert len(calls.inputs) == 2


@pytest.mark.parametrize('boundary', ['full', 'expanded', 'last'])
def test_write_failure_is_reached_and_never_advertises_new_ready_run(
        project, tmp_path, monkeypatch, boundary):
    from poise.composition import tool_result_tools
    from poise.infrastructure import tool_results as storage
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultFailure, ToolResultPolicy
    scene = SessionScenario(project, tmp_path)
    first, _ = scene.stored(scene.input())
    # The healthy existing storage owner is a control even before new UUID support.
    assert Path(first['response_path']).is_file()
    sentinel = scene.root / 'foreign-owned'
    sentinel.write_bytes(b'preserve-me\x00')
    target = scene.policy['session'][boundary]
    reached = []
    original = storage.atomic_write
    def fail(path, *args, **kwargs):
        if Path(path).name == target:
            reached.append(str(path))
            raise OSError('F08_TEST_OWNED_WRITE_FAILURE')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(storage, 'atomic_write', fail)
    bound = read_project_checkout(scene.config, scene.checkout)
    owner = tool_result_tools(bound, ToolResultPolicy.parse(scene.policy).data, scene.env)
    outcome = None
    try:
        outcome = owner.execute(scene.input('mcp', key='write-failed-run'), 'agent')
    except ToolResultFailure as error:
        assert error.code == 'storage_write'
    assert reached, 'F08_STORAGE_FAILURE_BOUNDARY_NOT_REACHED'
    assert outcome is None, 'F08_FAILED_PUBLICATION_ADVERTISED_SUCCESS'
    assert sentinel.read_bytes() == b'preserve-me\x00'
    if 'last_path' in first:
        assert json.loads(Path(first['last_path']).read_bytes()) == {'uuid': first['run_id']}
        assert json.loads(Path(first['full_path']).read_bytes()) == cases()['bash-success']['response']


def test_expanded_uses_latest_saved_bytes_without_explicit_path(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input())
    second, _ = scene.stored(scene.input('mcp', key='actual-last'))
    before = scene.snapshot()
    answer = scene.service('expanded')
    assert answer['text'] == cases()['mcp']['expanded']
    assert answer['run_id'] == second['run_id']
    assert scene.snapshot() == before


def test_full_path_is_latest_and_supports_two_actual_line_ranges(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input('mcp'))
    second, _ = scene.stored(scene.input(key='actual-last'))
    answer = scene.service('full')
    full = Path(answer['full_path'])
    assert full.is_absolute() and full.is_file()
    assert full == Path(second['full_path']) and answer['run_id'] == second['run_id']
    assert json.loads(full.read_bytes()) == cases()['bash-success']['response']
    expected = literal('session-ranges.expected.json')
    for spec in expected:
        with full.open(encoding='utf-8', newline='') as source:
            observed = ''.join(islice(source, spec['start'] - 1, spec['end']))
        assert observed == spec['text']
    assert 'FULL_ONLY_F08' in full.read_text()
    assert 'FULL_ONLY_F08' not in cases()['bash-success']['expanded']


def test_retrieval_never_reparses_or_recaptures_and_ordinary_poise_still_does(project, tmp_path):
    from poise.composition import tool_result_tools
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultPolicy
    scene = SessionScenario(project, tmp_path)
    prepare = prepare_owner()
    owner = tool_result_tools(read_project_checkout(scene.config, scene.checkout),
                              ToolResultPolicy.parse(scene.policy).data, scene.env)
    argv = [sys.executable, '-B', str(FIXTURES / 'session_producer.py'),
            '--stdout', str(FIXTURES / 'session-native-large.stdout.txt'),
            '--counter', str(scene.producer_count), '--exit-code', '0']
    actual_process = subprocess.run(['bash', '-c', shlex.join(argv)],
                                    capture_output=True, check=False)
    assert actual_process.returncode == 0 and actual_process.stderr == b''
    request = scene.input()
    request['response']['output'] = actual_process.stdout.decode()
    with ParserCalls(prepare) as calls:
        stored = owner.execute(request, 'agent')
        assert stored['preview'] == cases()['bash-success']['brief']
        stored_run = owner.execute({'schema': 'poise-tool-result-request-1',
                                    'operation': 'full'}, 'agent')['run_id']
        before = scene.snapshot()
        for operation in ('expanded', 'full', 'expanded'):
            answer = owner.execute({'schema': 'poise-tool-result-request-1',
                                    'operation': operation}, 'agent')
            assert answer['run_id'] == stored_run
        assert scene.snapshot() == before
        assert len(calls.inputs) == 1, 'F08_SERVICE_REPARSED_OR_CAPTURED'
        assert scene.producer_count.read_text() == '1'
        ordinary = owner.execute(scene.input('poise', key='ordinary-poise-tool'), 'agent')
        assert len(calls.inputs) == 2
        assert ordinary['preview'] == cases()['poise']['brief']
        assert owner.execute({'schema': 'poise-tool-result-request-1',
                              'operation': 'full'}, 'agent')['run_id'] != stored_run
    assert scene.producer_count.read_text() == '1'
    assert calls.inputs == [request['response'], cases()['poise']['response']]


@pytest.mark.parametrize('damage,operation,code', [
    ('last', 'expanded', 'source_missing'), ('expanded', 'expanded', 'source_missing'),
    ('full', 'full', 'source_missing'), ('metadata', 'full', 'source_invalid')])
def test_unavailable_last_material_reaches_damage_and_never_uses_old_run(
        project, tmp_path, damage, operation, code):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input())
    value, _ = scene.stored(scene.input('mcp', key='damaged-latest'))
    healthy = scene.service(operation)
    assert healthy['run_id'] == value['run_id'], 'F08_DAMAGE_CONTROL_NOT_REACHED'
    paths = {'last': value['last_path'], 'expanded': value['expanded_path'],
             'full': value['full_path'], 'metadata': value['response_path']}
    target = Path(paths[damage])
    assert target.is_file()
    if damage == 'metadata':
        target.write_bytes(b'not-json\n')
    else:
        target.unlink()
    before = scene.snapshot()
    exit_code, answer = scene.direct({'schema': 'poise-tool-result-request-1',
                                      'operation': operation})
    assert scene.calls[-1]['request']['operation'] == operation
    assert exit_code == 2 and answer['status'] == 'rejected'
    assert answer['error']['code'] == code
    assert 'full_path' not in answer and 'text' not in answer
    assert scene.snapshot() == before
