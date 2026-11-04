"""Universal output contracts through real owners; no installed-host claim.

The unknown observation schema and optional command-backed registration settings
are independent public contract fixtures. The gated transport invokes the actual
registration owner; it never implements persistence. Native envelopes are isolated.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import queue
import socket
import threading

import pytest

from tests.runtime_services.session_tool_results_support import SessionScenario
from tests.runtime_services.tool_results_support import FIXTURES, literal, wire


UNKNOWN_SCHEMA = 'poise-tool-result-unknown-1'


def owner_for(scene):
    from poise.composition import tool_result_tools
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultPolicy
    return tool_result_tools(read_project_checkout(scene.config, scene.checkout),
                             ToolResultPolicy.parse(scene.policy).data, scene.env)


def request_for(scene, case):
    data = literal('universal-cases.json')[case]
    return scene.native(deepcopy(data['response']), key=data['request_id'], tool=data['tool'])


def require_stored(code, value, label):
    assert code == 0 and value.get('status') == 'stored', (label, code, value)


def unknown_records(root):
    found = []
    for path in root.rglob('*.json'):
        value = json.loads(path.read_bytes())
        if isinstance(value, dict) and value.get('schema') == UNKNOWN_SCHEMA:
            found.append((path, value))
    return found


def assert_received_source(scene, value, expected):
    _, full, _, _ = scene.run_identity(value)
    assert wire(json.loads(full.read_bytes())) == wire(expected)
    return full


@pytest.mark.parametrize('case', ['unknown-tool', 'unknown-format', 'batch'])
def test_unmapped_native_has_bounded_agent_view_and_complete_machine_source(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    # The pre-existing valid request proves this is a functioning environment.
    guard, _ = scene.stored(scene.input(key='known-before-universal'))
    request = request_for(scene, case)
    original = deepcopy(request)
    code, machine = scene.direct(request, 'machine')
    require_stored(code, machine, 'UNIVERSAL_NATIVE_FALLBACK_MISSING')
    assert request == original and wire(machine['response']) == wire(original['event']['tool_response'])
    full = assert_received_source(scene, machine, original['event']['tool_response'])
    replay_code, replay = scene.direct(deepcopy(request), 'machine')
    require_stored(replay_code, replay, 'EXACT_CAPTURE_REPLAY_FAILED')
    assert replay['run_id'] == machine['run_id'] and replay['full_path'] == str(full)
    code, agent = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'view',
         'response_path': machine['response_path'], 'view': 'brief'})
    require_stored(code, agent, 'UNIVERSAL_READONLY_VIEW_MISSING')
    assert agent['run_id'] == machine['run_id'] != guard['run_id']
    assert agent['exit_code'] == literal('universal-cases.json')[case]['exit_code']
    assert request['event']['tool_name'] in agent['preview']
    assert 'unknown' in agent['preview'].casefold(), 'UNKNOWN_LIMITATION_NOT_VISIBLE'
    assert 'RAW_ONLY_' not in agent['preview']
    assert len(wire(agent).decode()) <= scene.policy['display']['output_chars']
    assert len(wire(agent)) <= scene.policy['display']['output_bytes']
    assert json.loads(full.read_bytes()) == original['event']['tool_response']


def test_unknown_capture_keeps_both_complete_streams_and_structured_fields(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    data = literal('universal-cases.json')['unknown-tool']
    request = scene.request(request_id='unknown-with-streams',
        tool={'kind': 'mcp', 'name': data['tool']}, response=deepcopy(data['response']))
    before_stdout, before_stderr = scene.out.read_bytes(), scene.err.read_bytes()
    code, machine = scene.direct(request, 'machine')
    require_stored(code, machine, 'UNKNOWN_CAPTURE_HAS_NO_COMPLETE_MACHINE_ROUTE')
    assert wire(machine['response']) == wire(data['response']) and machine['exit_code'] == 7
    for channel, expected in [('stdout', before_stdout), ('stderr', before_stderr)]:
        stream = machine['streams'][channel]
        assert Path(stream['path']).read_bytes() == expected
        assert stream['sha256'] == hashlib.sha256(expected).hexdigest()
    assert scene.out.read_bytes() == before_stdout and scene.err.read_bytes() == before_stderr
    assert_received_source(scene, machine, data['response'])


def test_pending_source_is_saved_and_presented_without_claiming_completion(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    code, value = scene.direct(request_for(scene, 'pending'), 'machine')
    require_stored(code, value, 'PENDING_RESULT_BYPASSES_CAPTURE')
    assert value['exit_code'] is None
    assert value['capture'] == {'state': 'unknown', 'reason': 'native_process_pending'}
    full = assert_received_source(scene, value, literal('universal-cases.json')['pending']['response'])
    code, agent = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'view',
                               'response_path': value['response_path'], 'view': 'brief'})
    require_stored(code, agent, 'PENDING_RESULT_HAS_NO_BOUNDED_VIEW')
    assert agent['capture'] == value['capture'] and agent['exit_code'] is None
    assert 'pending' in agent['preview'].casefold() and 'RAW_ONLY_' not in agent['preview']
    assert Path(value['full_path']) == full and 'full_path' not in agent


def async_cli(scene, request):
    delivered, result = threading.Event(), queue.Queue()

    def invoke():
        try:
            result.put(scene.direct(request, 'machine'))
        except Exception as error:
            result.put(error)
        finally:
            delivered.set()

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    return delivered, result, thread


def registry_fixture(scene, listener, *, fail=False):
    root = scene.root / 'unknown-observations'
    if fail:
        root.write_text('fixture: not a directory')
    command = [scene.python, '-B', '-m', 'poise', 'tool-result',
               '--config', str(scene.config), '--checkout', str(scene.checkout),
               '--policy', str(scene.policy_path), '--audience', 'machine']
    witness = scene.parent / 'registration-witness.json'
    scene.policy['unknown_inputs'] = {
        'registry_root': str(root),
        'registration_command': [scene.python, '-B', str(FIXTURES / 'unknown_registrar_proxy.py'),
                                 str(listener.getsockname()[1]), str(witness), *command],
        'registration_cwd': str(scene.parent),
    }
    scene.save_policy()
    return root, witness


def assert_registration_started(listener, delivered, result):
    try:
        channel, _ = listener.accept()
    except TimeoutError:
        actual = result.get_nowait() if delivered.is_set() else 'presentation still running'
        pytest.fail('UNKNOWN_REGISTRATION_COMMAND_NOT_STARTED: ' + repr(actual))
    channel.settimeout(5)
    reader = channel.makefile('rb')
    assert reader.readline() == b'ready\n'
    return channel, reader


def assert_agent_view_while_registration_is_gated(scene, machine, request):
    full = assert_received_source(scene, machine, request['event']['tool_response'])
    before = full.read_bytes()
    code, text, err = scene.invoke(
        {'schema': 'poise-tool-result-request-1', 'operation': 'view',
         'response_path': machine['response_path'], 'view': 'brief'}, 'agent')
    assert err == ''
    agent = json.loads(text)
    require_stored(code, agent, 'GATED_REGISTRATION_HAS_NO_AGENT_PREVIEW')
    scene.bounded(text)
    assert agent['preview'] and request['event']['tool_name'] in agent['preview']
    assert 'unknown' in agent['preview'].casefold() and 'RAW_ONLY_' not in text
    assert agent['exit_code'] == machine['exit_code'] == request['exit_code']
    assert agent['capture'] == machine['capture']
    assert agent['run_id'] == machine['run_id']
    assert not {'response_path', 'full_path', 'expanded_path', 'last_path'} & agent.keys()
    assert machine['full_path'] == str(full) and full.read_bytes() == before
    return agent


@pytest.mark.parametrize('case', ['unknown-tool', 'unknown-format'])
def test_unknown_registration_is_async_complete_and_exact_repeat_is_one_observation(
        project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input(key='known-before-registry'))
    request = request_for(scene, case)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        listener.settimeout(5)
        root, witness = registry_fixture(scene, listener)
        delivered, result, thread = async_cli(scene, request)
        channel, reader = assert_registration_started(listener, delivered, result)
        with channel, reader:
            try:
                assert delivered.wait(5), 'CLI_PRESENTATION_WAITS_FOR_REGISTRATION'
                returned = result.get_nowait()
                assert isinstance(returned, tuple), returned
                code, machine = returned
                require_stored(code, machine, 'UNKNOWN_REGISTRATION_CHANGED_TOOL_RESULT')
                full = assert_received_source(scene, machine, request['event']['tool_response'])
                assert not root.exists() or not unknown_records(root)
                assert_agent_view_while_registration_is_gated(scene, machine, request)
                assert not root.exists() or not unknown_records(root)
            finally:
                channel.sendall(b'G')
                thread.join(5)
            assert reader.readline() == b'0\n', 'REAL_REGISTRATION_OWNER_FAILED'
        assert not thread.is_alive()
        actual = json.loads(witness.read_bytes())
        assert actual['exit_code'] == 0 and actual['stderr'] == ''
        records = unknown_records(root)
        assert len(records) == 1, ('UNKNOWN_REGISTRY_NOT_ONE_LOGICAL_RECORD', records)
        path, record = records[0]
        expected = deepcopy(literal('unknown-observations.json')[case])
        expected['full_source'] = {
            'path': str(full), 'sha256': hashlib.sha256(full.read_bytes()).hexdigest()}
        assert record == expected and actual['observation'] == expected
        before = path.read_bytes()
        # Replay the exact registry operation twice through its actual owner.
        replay = {'schema': 'poise-tool-result-request-1', 'operation': 'register_unknown',
                  'observation': deepcopy(expected)}
        for _ in range(2):
            repeat_code, repeat = scene.direct(replay, 'machine')
            assert repeat_code == 0, repeat
        assert unknown_records(root) == [(path, expected)] and path.read_bytes() == before
        assert json.loads(full.read_bytes()) == request['event']['tool_response']


def test_registry_failure_preserves_generic_view_and_original_tool_outcome(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input(key='known-before-registry-failure'))
    request = request_for(scene, 'unknown-format')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(5)
        root, witness = registry_fixture(scene, listener, fail=True)
        delivered, result, thread = async_cli(scene, request)
        channel, reader = assert_registration_started(listener, delivered, result)
        with channel, reader:
            try:
                assert delivered.wait(5), 'FAILED_REGISTRATION_BLOCKS_CLI_RESULT'
                returned = result.get_nowait()
                assert isinstance(returned, tuple), returned
                code, machine = returned
                require_stored(code, machine, 'REGISTRY_FAILURE_CHANGED_TOOL_RESULT')
                assert_agent_view_while_registration_is_gated(scene, machine, request)
                assert root.read_text() == 'fixture: not a directory'
            finally:
                channel.sendall(b'G')
                thread.join(5)
            assert reader.readline() == b'2\n', 'STORAGE_FAILURE_NOT_REPORTED_BY_REGISTRAR'
        actual = json.loads(witness.read_bytes())
        assert actual['exit_code'] == 2 and root.read_text() == 'fixture: not a directory'
        code, agent = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'view',
                                   'response_path': machine['response_path'], 'view': 'brief'})
        require_stored(code, agent, 'GENERIC_VIEW_LOST_AFTER_REGISTRY_FAILURE')
        assert machine['exit_code'] == agent['exit_code'] == 7
        assert 'unknown' in agent['preview'].casefold() and 'RAW_ONLY_' not in agent['preview']
        assert_received_source(scene, machine, request['event']['tool_response'])


@pytest.mark.parametrize('case', ['completed', 'failed', 'empty-page'])
def test_observed_thread_wire_has_specialized_essential_fields(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input(key='known-before-thread'))
    scene.policy['parsers']['mcp__fixture__read_thread'] = literal('thread-profile.json')
    scene.save_policy()
    data = literal('thread-cases.json')[case]
    response = {'content': [{'type': 'text', 'text': json.dumps(data['wire'], ensure_ascii=False)}]}
    request = scene.native(response, key='thread-' + case, tool='mcp__fixture__read_thread')
    code, machine = scene.direct(request, 'machine')
    require_stored(code, machine, 'OBSERVED_THREAD_PROFILE_UNSUPPORTED')
    _, full, expanded, _ = scene.run_identity(machine)
    assert json.loads(full.read_bytes()) == response
    assert expanded.read_text() == data['expanded']
    code, agent = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'view',
                               'response_path': machine['response_path'], 'view': 'brief'})
    require_stored(code, agent, 'THREAD_SPECIALIZED_VIEW_MISSING')
    assert agent['preview'] == data['brief']
    assert 'RAW_ONLY_' not in expanded.read_text() + agent['preview']


def test_generic_route_uses_two_external_roots_without_cross_project_writes(project, tmp_path):
    parents = [tmp_path / 'poise-consumer', tmp_path / 'erp-consumer']
    scenes = []
    values = []
    for parent in parents:
        parent.mkdir()
        scene = SessionScenario(project, parent)
        code, machine = scene.direct(request_for(scene, 'batch'), 'machine')
        require_stored(code, machine, 'PORTABLE_GENERIC_CONSUMER_MISSING')
        assert Path(machine['full_path']).is_relative_to(parent)
        assert_received_source(scene, machine, literal('universal-cases.json')['batch']['response'])
        scenes.append(scene)
        values.append(machine)
    assert values[0]['full_path'] != values[1]['full_path']
    first = {str(p.relative_to(parents[0])): p.read_bytes()
             for p in parents[0].rglob('*') if p.is_file()}
    owner_for(scenes[1]).execute({'schema': 'poise-tool-result-request-1', 'operation': 'view',
                                'response_path': values[1]['response_path'], 'view': 'brief'}, 'agent')
    assert {str(p.relative_to(parents[0])): p.read_bytes()
            for p in parents[0].rglob('*') if p.is_file()} == first
    import poise
    assert Path(poise.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2] / 'src')


def test_erp_python312_consumes_wire_from_explicit_python315_provider(project, tmp_path):
    import subprocess
    import sys
    # These are the already available, explicitly verified consumer/provider runtimes.
    assert sys.version_info[:2] == (3, 15)
    for consumer in ('poise-wire', 'erp-wire'):
        parent = tmp_path / consumer
        parent.mkdir()
        scene = SessionScenario(project, parent)
        provider = [scene.python, '-B', '-m', 'poise', 'tool-result',
                    '--config', str(scene.config), '--checkout', str(scene.checkout),
                    '--policy', str(scene.policy_path), '--audience', 'machine']
        client = ['/usr/bin/python3', '-B', str(FIXTURES / 'erp_wire_consumer.py'), *provider]
        for request, feature in [(scene.input(key='known-wire'), False),
                                 (request_for(scene, 'batch'), True)]:
            process = subprocess.run(client, input=wire(request), capture_output=True,
                                     env=scene.env, cwd=parent, check=False)
            assert process.returncode == 0 and process.stderr == b'', process.stderr
            value = json.loads(process.stdout)
            assert value['consumer_version'] == [3, 12]
            assert value['client_imported_poise'] is False
            assert value['provider_identity']['version'] == [3, 15]
            assert Path(value['provider_identity']['module']).resolve().is_relative_to(
                Path(__file__).resolve().parents[2] / 'src')
            assert value['stderr'] == ''
            require_stored(value['provider_exit_code'], value['response'],
                           'ERP_WIRE_GENERIC_CAPABILITY_MISSING' if feature else 'ERP_WIRE_ENVIRONMENT_BROKEN')
            expected = (literal('universal-cases.json')['batch']['response'] if feature
                        else request['response'])
            full = assert_received_source(scene, value['response'], expected)
            assert full.is_relative_to(parent)
