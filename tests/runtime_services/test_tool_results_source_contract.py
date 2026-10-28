"""B: actual process bytes and isolated native source producer/consumer.

The test-owned native JSON value models the accepted interface. These are not
Codex PostToolUse or installed-host observations.
"""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.runtime_services.session_tool_results_support import SessionScenario
from tests.runtime_services.tool_results_support import FIXTURES, literal


@pytest.mark.parametrize('case', ['healthy', 'warning', 'infix', 'ambiguous', 'other-literal'])
def test_native_loss_marker_producer_uses_declared_profile(project, tmp_path, case):
    from poise.modules.result_views.tool_results import native_snapshot
    scene = SessionScenario(project, tmp_path)
    data = literal('code-native-loss-cases.json')[case]
    request = scene.native(data['response'], tool=data['tool'])
    original = deepcopy(request['event'])
    actual = native_snapshot(request['event'], scene.policy)
    assert actual['response'] == data['response'] and request['event'] == original
    assert actual['capture'] == {'state': data['state'], 'reason': data['reason']}, (
        'F08_NATIVE_LOSS_FACT', case, actual['capture'])
    assert actual['exit_code'] == data['response'].get('exit_code')
    print(json.dumps({'boundary': 'actual-native-loss-producer', 'case': case,
                      'input': original, 'actual': actual}, ensure_ascii=False))


@pytest.mark.parametrize('case', ['healthy', 'warning', 'infix', 'ambiguous', 'other-literal'])
def test_native_loss_marker_public_full_consumer(project, tmp_path, case):
    from poise.composition import tool_result_tools
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultPolicy
    from tests.runtime_services.session_tool_results_support import ParserCalls, prepare_owner
    scene = SessionScenario(project, tmp_path)
    previous, _ = scene.stored(scene.input(key='complete-before-marker'))
    data = literal('code-native-loss-cases.json')[case]
    request = scene.native(data['response'], key='current-marker-' + case, tool=data['tool'])
    original = deepcopy(request)
    code, actual = scene.direct(request, audience='machine')
    assert code == 0 and actual['status'] == 'stored'
    assert actual['response'] == data['response'] and request == original
    assert actual['capture'] == {'state': data['state'], 'reason': data['reason']}, (
        'F08_NATIVE_LOSS_FULL_CONSUMER', case, actual['capture'])
    current_id, full, _, last = scene.run_identity(actual)
    assert current_id != previous['run_id']
    assert json.loads(full.read_bytes()) == data['response']
    assert actual['exit_code'] == data['response'].get('exit_code')
    before = scene.snapshot()
    full_code, full_result = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'full'})
    if data['state'] == 'complete':
        assert full_code == 0 and full_result['status'] == 'full'
        assert full_result['full_path'] == str(full) and full_result['run_id'] == current_id
    else:
        assert full_code == 2 and full_result['status'] == 'rejected'
        assert full_result['error']['code'] == 'source_incomplete'
        assert 'full_path' not in full_result
    owner = tool_result_tools(read_project_checkout(scene.config, scene.checkout),
                              ToolResultPolicy.parse(scene.policy).data, scene.env)
    with ParserCalls(prepare_owner()) as calls:
        expanded = owner.execute({'schema': 'poise-tool-result-request-1', 'operation': 'expanded'}, 'agent')
    assert calls.inputs == [], 'F08_MARKER_SERVICE_REPARSED'
    assert expanded['run_id'] == current_id and expanded['capture'] == actual['capture']
    assert json.loads(last.read_bytes()) == {'uuid': current_id}
    assert scene.snapshot() == before, 'F08_MARKER_SERVICE_REPLACED_SOURCE_OR_LAST'
    print(json.dumps({'boundary': 'actual-native-loss-full-consumer', 'case': case,
                      'request': original, 'record': actual, 'full_result': full_result,
                      'expanded': expanded}, ensure_ascii=False))


@pytest.mark.parametrize('case,exit_code', [('healthy', 0), ('failure', 7), ('large', 0)])
def test_terminal_native_source_is_classified_before_capture(project, tmp_path, case, exit_code):
    scene = SessionScenario(project, tmp_path)
    channel = 'stderr' if case == 'failure' else 'stdout'
    source = FIXTURES / ('session-native-' + case + '.' + channel + '.txt')
    argv = [sys.executable, '-B', str(FIXTURES / 'session_producer.py'),
            '--' + channel, str(source), '--counter', str(scene.producer_count),
            '--exit-code', str(exit_code)]
    cp = subprocess.run(['bash', '-c', shlex.join(argv)], capture_output=True, check=False)
    assert cp.returncode == exit_code
    assert (cp.stderr if channel == 'stderr' else cp.stdout) == source.read_bytes()
    assert (cp.stdout if channel == 'stderr' else cp.stderr) == b''
    # This complete value belongs to the isolated native-interface fixture.
    response = deepcopy(literal('session-native-source-cases.json')['healthy'])
    response.update(output=(cp.stdout + cp.stderr).decode(), exit_code=cp.returncode)
    if case != 'healthy':
        response.pop('original_token_count')
    response['machine_extra'] = literal('session-parser-cases.json')['poise']['response']
    code, value = scene.direct(scene.native(response), audience='machine')
    assert code == 0 and value['status'] == 'stored'
    assert value['response'] == response, 'F08_NATIVE_MACHINE_PAYLOAD_CHANGED'
    assert value['capture'] == {'state': 'complete', 'reason': None}, 'F08_NATIVE_SOURCE_NOT_CLASSIFIED'
    assert value['streams'] == {'stdout': None, 'stderr': None}, 'F08_MERGED_OUTPUT_INVENTED_STREAMS'
    saved = json.loads(Path(value['response_path']).read_bytes())
    assert saved['response'] == response and saved['exit_code'] == exit_code
    _, full, expanded, _ = scene.run_identity(value)
    assert json.loads(full.read_bytes()) == response
    assert scene.producer_count.read_text() == '1'
    if case == 'large':
        assert len(response['output']) > 512
        assert 'FULL_ONLY_F08' not in expanded.read_text()
    print(json.dumps({'actual_process_argv': argv, 'exit_code': cp.returncode,
                      'full_native_fixture': response, 'captured': saved}, ensure_ascii=False))


def test_clipped_native_envelope_stays_original_and_partial(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    response = literal('session-native-source-cases.json')['partial']
    code, value = scene.direct(scene.native(response), audience='machine')
    assert code == 0 and value['response'] == response
    assert value['capture']['state'] == 'partial', 'F08_NATIVE_TRUNCATION_NOT_CLASSIFIED'
    assert isinstance(value['capture']['reason'], str) and value['capture']['reason']
    assert value['exit_code'] == 0 and response['original_token_count'] == 255
    assert 'tokens truncated' in value['response']['output']
    assert len(response['output']) == 147


def test_latest_partial_cannot_return_older_complete_full_path(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input())
    response = literal('session-native-source-cases.json')['partial']
    # Existing public capture arranges truthful already-partial source facts;
    # the other test separately proves the actual native classifier producer.
    request = scene.input(key='last-partial')
    request.update(response=response, capture={'state': 'partial', 'reason': 'native_output_truncated'})
    partial, _ = scene.stored(request)
    code, actual = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'full'})
    assert code == 2 and actual.get('status') == 'rejected'
    assert actual['error']['code'] == 'source_incomplete', 'F08_PARTIAL_FULL_SERVICE_NOT_GUARDED'
    assert 'full_path' not in actual
    expanded = scene.service('expanded')
    assert expanded['run_id'] == partial['run_id']
    assert expanded['capture']['state'] == 'partial'


@pytest.mark.parametrize('case,status,code', [
    ('missing', 'rejected', 'source_missing'),
    ('pending', 'pending', None),
    ('unknown-wire', 'rejected', 'unmapped_input')])
def test_native_missing_pending_and_unknown_are_not_complete(project, tmp_path, case, status, code):
    scene = SessionScenario(project, tmp_path)
    # A real ready predecessor makes replacing last on pending observable.
    if case == 'pending':
        previous, _ = scene.stored(scene.input(key='ready-before-native-unavailable'))
        previous_id, previous_full, previous_expanded, last = scene.run_identity(previous)
        ready_root = previous_full.parent.parent
        full_name = scene.policy['session']['full']
        expanded_name = scene.policy['session']['expanded']

    def ready_publications():
        # Observe published pairs only, not private pending/staging bookkeeping.
        return {str(folder): ((folder / full_name).read_bytes(),
                              (folder / expanded_name).read_bytes())
                for folder in ready_root.iterdir()
                if folder.is_dir() and not folder.name.startswith('.')
                and (folder / full_name).is_file() and (folder / expanded_name).is_file()}

    if case == 'pending':
        ready_before, last_before = ready_publications(), last.read_bytes()
        assert str(previous_full.parent) in ready_before
    response = literal('session-native-source-cases.json')['healthy']
    if case == 'pending':
        response = {'session_id': 43, 'output': 'still running'}
    elif case == 'unknown-wire':
        response = ['not the declared native-json profile']
    request = scene.native(response, present=case != 'missing')
    before = scene.snapshot()
    exit_code, value = scene.direct(request)
    assert value.get('status') == status, 'F08_UNKNOWN_NATIVE_SOURCE_ADMITTED'
    if code:
        assert exit_code == 2 and value['error']['code'] == code, 'F08_NATIVE_SOURCE_GUARD_MISSING'
    else:
        assert exit_code == 0 and value['capture']['state'] == 'unknown'
        assert last.read_bytes() == last_before, 'F08_PENDING_REPLACED_READY_LAST'
        assert ready_publications() == ready_before, 'F08_PENDING_PUBLISHED_READY_RUN'
        assert scene.service('full')['run_id'] == previous_id
        assert scene.service('expanded')['run_id'] == previous_id
        terminal = literal('session-native-source-cases.json')['healthy']
        callback = scene.native(terminal, key=request['event']['tool_use_id'])
        assert callback['event']['session_id'] == request['event']['session_id']
        assert callback['event']['tool_use_id'] == request['event']['tool_use_id']
        terminal_code, completed = scene.direct(callback, audience='machine')
        assert terminal_code == 0 and completed['capture']['state'] == 'complete'
        assert completed['response'] == terminal, 'F08_PENDING_TERMINAL_ORIGINAL_CHANGED'
        completed_id, full, expanded, completed_last = scene.run_identity(completed)
        assert completed_id != previous_id and completed_last == last
        assert json.loads(full.read_bytes()) == terminal
        ready_after = ready_publications()
        assert set(ready_after) - set(ready_before) == {str(full.parent)}, (
            'F08_PENDING_TERMINAL_READY_PUBLICATION_COUNT', ready_after.keys())
        assert {key: ready_after[key] for key in ready_before} == ready_before
        assert scene.service('full')['run_id'] == completed_id
        print(json.dumps({'boundary': 'pending-terminal-ready-publication',
                          'session': callback['event']['session_id'],
                          'tool_use_id': callback['event']['tool_use_id'],
                          'previous_run_id': previous_id, 'terminal_run_id': completed_id,
                          'ready_before': list(ready_before), 'ready_after': list(ready_after),
                          'full_path': str(full), 'expanded_path': str(expanded)}))
    if case != 'pending':
        assert scene.snapshot() == before


@pytest.mark.parametrize('case,expected', [('healthy', 'complete'), ('partial', 'partial')])
def test_token_count_is_not_the_completeness_oracle(project, tmp_path, case, expected):
    scene = SessionScenario(project, tmp_path)
    response = literal('session-native-source-cases.json')[case]
    assert response['original_token_count'] == 255
    code, value = scene.direct(scene.native(response), audience='machine')
    assert code == 0 and value['response'] == response
    assert value['capture']['state'] == expected, 'F08_TOKEN_COUNT_ONLY_CLASSIFICATION'


def test_input_limit_refusal_keeps_original_data(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    response = literal('session-native-source-cases.json')['healthy']
    scene.policy['input']['max_bytes'] = 32
    scene.save_policy()
    before = scene.snapshot()
    code, value = scene.direct(scene.native(response))
    assert code == 2 and value['status'] == 'rejected'
    assert value['error']['code'] == 'invalid_request'
    assert scene.snapshot() == before
    assert response == literal('session-native-source-cases.json')['healthy']
