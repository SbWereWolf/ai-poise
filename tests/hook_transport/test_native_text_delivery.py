"""Isolated real dispatcher contracts for the observed native string wire.

Public Start/configuration/dispatch and existing saved-service owners are real.
No live identity, permission replacement, event replay or result mapper is used.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import shlex
import subprocess

import pytest

from tests.hook_transport.architecture_support import Chain
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.native_wire_support import (
    NativeCalls, select_text, select_work_text, wire_case,
)
from tests.runtime_services.session_tool_results_support import ParserCalls, cases, prepare_owner
from tests.runtime_services.tool_results_support import literal


@pytest.mark.parametrize('case', ['healthy', 'empty', 'json-looking', 'unicode-nul'])
def test_actual_shaped_text_reaches_real_dispatcher_and_delivers_prepared_brief(project, tmp_path, case):
    chain = Chain(project, tmp_path, 'F08_NATIVE_TEXT_DELIVERY')
    scene = scene_for_chain(chain, project, tmp_path)
    select_text(scene)
    configure_chain(chain, scene)
    expected = wire_case(case)
    event = scene.native(expected['raw'], key='native-text-' + case)['event']
    event['session_id'] = chain.session
    original = deepcopy(event)
    with ParserCalls(prepare_owner()) as prepared, NativeCalls() as native:
        response = chain.invoke(event, event_name='PostToolUse')
    _, delivery = chain.delivery()
    assert {key: delivery['event'][key] for key in original} == original
    assert delivery['event']['tool_response'] == expected['raw'] and event == original
    assert delivery['event_receipt']['event'] == 'PostToolUse'
    assert native.events and len(native.events) == 1, 'NATIVE_TEXT_HOOK_CLASSIFIER_NOT_REACHED'
    assert native.events[0]['tool_response'] == expected['raw']
    invocation, = delivery['invocations']
    assert invocation['configuration']['handler_id'] == 'tool-result'
    print(json.dumps({'boundary': 'public-actual-shaped-native-text', 'event': original,
                      'delivery': delivery, 'response': response}, ensure_ascii=False))
    assert response['hookSpecificOutput']['additionalContext'] == expected['brief'], (
        'NATIVE_TEXT_HOOK_BRIEF_NOT_DELIVERED', response)
    assert response['continue'] is False
    assert prepared.inputs == [expected['raw']], 'NATIVE_TEXT_NOT_PREPARED_EXACTLY_ONCE'
    answer = scene.service('full')
    assert Path(answer['full_path']).read_bytes() == expected['raw'].encode()
    assert scene.service('expanded')['text'] == expected['expanded']


@pytest.mark.parametrize('operation', ['expanded', 'full'])
def test_actual_text_service_response_crosses_hook_without_new_capture(project, tmp_path, operation):
    chain = Chain(project, tmp_path, 'F08_NATIVE_TEXT_SERVICE')
    scene = scene_for_chain(chain, project, tmp_path)
    select_text(scene)
    configure_chain(chain, scene)
    expected = wire_case('healthy')
    event = scene.native(expected['raw'], key='native-text-before-service')['event']
    event['session_id'] = chain.session
    with ParserCalls(prepare_owner()) as calls:
        first = chain.invoke(event, event_name='PostToolUse')
        assert first['hookSpecificOutput']['additionalContext'] == expected['brief'], (
            'NATIVE_TEXT_SERVICE_PREDECESSOR_FAILED', first)
        assert calls.inputs == [expected['raw']]
        saved = scene.service('full')
        run_id = saved['run_id']
        full = Path(saved['full_path'])
        assert full.read_bytes() == expected['raw'].encode()
        before = scene.snapshot()
        service = scene.service(operation)
        actual_cli = scene.calls[-1]
        assert actual_cli['exit_code'] == 0 and actual_cli['stderr'] == ''
        assert json.loads(actual_cli['stdout']) == service
        service_event = scene.native(actual_cli['stdout'], key='native-text-service-' + operation)['event']
        service_event['session_id'] = chain.session
        service_event['tool_input']['command'] = shlex.join(actual_cli['argv'])
        original = deepcopy(service_event)
        delivered = chain.invoke(service_event, event_name='PostToolUse')
        actual = service['text'] if operation == 'expanded' else service['full_path']
        assert delivered['hookSpecificOutput']['additionalContext'] == actual
        assert delivered['continue'] is False and service_event == original
        assert chain.delivery()[1]['event']['tool_response'] == actual_cli['stdout']
        assert calls.inputs == [expected['raw']], 'NATIVE_TEXT_SERVICE_REPARSED_OR_RECAPTURED'
        assert scene.snapshot() == before, 'NATIVE_TEXT_SERVICE_REPLACED_LAST_OR_RUN_FILES'
        assert scene.service('full')['run_id'] == run_id


def test_ordinary_poise_text_envelope_and_same_json_as_bash_have_distinct_sources(project, tmp_path):
    from poise.interfaces.work import write_result
    from tests.runtime_services.test_output_boundary_agent import AgentBoundaryTests
    chain = Chain(project, tmp_path, 'F08_NATIVE_TEXT_POISE')
    scene = scene_for_chain(chain, project, tmp_path)
    producer = AgentBoundaryTests()
    producer.setUp()
    try:
        producer.runtime.session = chain.session
        body = deepcopy(cases()['poise']['response'])
        output = io.StringIO()
        write_result(producer.runtime, body, output)
        envelope = json.loads(output.getvalue())
        assert json.loads(Path(envelope['response_path']).read_bytes()) == body
        select_work_text(scene, producer.root)
        configure_chain(chain, scene)
        event = scene.native(output.getvalue(), key='ordinary-poise-text')['event']
        event['session_id'] = chain.session
        event['tool_input']['command'] = 'python -m poise work'
        original = deepcopy(event)
        with ParserCalls(prepare_owner()) as calls:
            delivered = chain.invoke(event, event_name='PostToolUse')
        assert chain.delivery()[1]['event']['tool_response'] == output.getvalue()
        assert event == original
        assert delivered['hookSpecificOutput']['additionalContext'] == 'Poise: verified', (
            'SAVED_WORK_TEXT_NOT_READ_THROUGH_SAVED_SOURCE', delivered)
        assert calls.inputs == [body]
        previous = scene.service('full')
        assert json.loads(Path(previous['full_path']).read_bytes()) == body
        assert scene.service('expanded')['text'] == 'Poise: verified\nNext: inspect tests'

        # SAME JSON text, explicitly ordinary Bash: it stays text, not a saved
        # source lookup or a manufactured process execution envelope.
        ordinary = scene.native(output.getvalue(), key='json-looking-ordinary-bash')['event']
        ordinary['session_id'] = chain.session
        ordinary['tool_input']['command'] = 'printf literal-json'
        response = chain.invoke(ordinary, event_name='PostToolUse')
        assert response['hookSpecificOutput']['additionalContext'] == (
            'Bash: output received; exit unavailable')
        last = scene.service('full')
        assert last['run_id'] != previous['run_id']
        assert Path(last['full_path']).read_bytes() == output.getvalue().encode()
    finally:
        producer.doCleanups()


def test_mcp_object_control_survives_explicit_text_bash_profile(project, tmp_path):
    chain = Chain(project, tmp_path, 'F08_NATIVE_TEXT_MCP_CONTROL')
    scene = scene_for_chain(chain, project, tmp_path)
    select_text(scene)
    tool = 'mcp__fixture__projects'
    scene.policy['parsers'][tool] = literal('mcp_content/profile.json')
    scene.save_policy()
    configure_chain(chain, scene, matcher='Bash|mcp__fixture__projects')
    source = literal('mcp_content/inputs.json')['actual-wire-shape']
    expected = literal('mcp_content/expected.json')['actual-wire-shape']
    event = scene.native(source, key='mcp-native-text-control', tool=tool)['event']
    event['session_id'] = chain.session
    original = deepcopy(event)
    with ParserCalls(prepare_owner()) as calls:
        delivered = chain.invoke(event, event_name='PostToolUse')
    assert delivered['hookSpecificOutput']['additionalContext'] == expected['brief']
    assert calls.inputs == [source] and event == original
    assert chain.delivery()[1]['event']['tool_response'] == source
    full = scene.service('full')
    assert json.loads(Path(full['full_path']).read_bytes()) == source
    assert scene.service('expanded')['text'] == expected['expanded']


def test_partial_full_refusal_crosses_hook_without_recapture(project, tmp_path):
    chain = Chain(project, tmp_path, 'F08_SERVICE_REFUSAL')
    scene = scene_for_chain(chain, project, tmp_path)
    select_text(scene)
    configure_chain(chain, scene)
    partial = wire_case('partial')
    event = scene.native(partial['raw'], key='partial-before-refusal')['event']
    event['session_id'] = chain.session
    expected_refusal = literal('native_wire/service-refusal.json')

    with ParserCalls(prepare_owner()) as prepared:
        chain.invoke(event, event_name='PostToolUse')
        assert prepared.inputs == [partial['raw']], 'PARTIAL_PRODUCER_NOT_REACHED'
        expanded = scene.service('expanded')
        assert expanded['capture'] == {
            'state': 'partial', 'reason': 'native_output_truncated',
        }
        before = scene.snapshot()
        last_name, = (name for name in before if Path(name).name == 'last-tool.json')
        assert json.loads(before[last_name]) == {'uuid': expanded['run_id']}
        full_name, = (name for name in before if Path(name).name == 'full')
        assert before[full_name] == partial['raw'].encode()
        directories = sorted(str(path.relative_to(scene.root))
                             for path in scene.root.rglob('*') if path.is_dir())

        # The actual public expanded response already passes the same boundary.
        expanded_cli = scene.calls[-1]
        expanded_event = scene.native(expanded_cli['stdout'], key='partial-expanded')['event']
        expanded_event['session_id'] = chain.session
        expanded_event['tool_input']['command'] = shlex.join(expanded_cli['argv'])
        delivered = chain.invoke(expanded_event, event_name='PostToolUse')
        assert delivered['hookSpecificOutput']['additionalContext'] == expanded['text']
        assert scene.snapshot() == before and prepared.inputs == [partial['raw']]

        request = {'schema': 'poise-tool-result-request-1', 'operation': 'full'}
        code, text, stderr = scene.invoke(request)
        assert code == 2 and stderr == ''
        assert json.loads(text) == expected_refusal
        scene.bounded(text)
        assert scene.snapshot() == before, 'PUBLIC_FULL_REFUSAL_MUTATED_SOURCE'
        actual_cli = scene.calls[-1]
        refusal_event = scene.native(text, key='actual-full-refusal')['event']
        refusal_event['session_id'] = chain.session
        refusal_event['tool_input']['command'] = shlex.join(actual_cli['argv'])
        original = deepcopy(refusal_event)
        response = chain.invoke(refusal_event, event_name='PostToolUse')
        _, delivery = chain.delivery()
        assert delivery['event']['tool_response'] == actual_cli['stdout']
        after = scene.snapshot()
        print(json.dumps({
            'boundary': 'actual-partial-full-refusal-post',
            'partial_event': event, 'expanded_cli': expanded_cli, 'full_cli': actual_cli,
            'refusal_event': original, 'delivery': delivery, 'response': response,
            'original_last': json.loads(before[last_name]),
            'actual_last': json.loads(after[last_name]),
            'original_files': sorted(before), 'actual_files': sorted(after),
            'parser_inputs': prepared.inputs,
        }, ensure_ascii=False))
        assert after == before, 'SERVICE_REFUSAL_RECAPTURED_OR_REPLACED_LAST'
        assert sorted(str(path.relative_to(scene.root))
                      for path in scene.root.rglob('*') if path.is_dir()) == directories
        assert prepared.inputs == [partial['raw']], 'SERVICE_REFUSAL_REPREPARED'
        context = response['hookSpecificOutput']['additionalContext']
        assert context.startswith('{'), ('HARNESS_REFUSAL_NOT_DELIVERED', context)
        assert json.loads(context) == expected_refusal
        assert len(context) <= scene.policy['display']['output_chars']
        assert len(context.encode()) <= scene.policy['display']['output_bytes']
        assert response['continue'] is False and refusal_event == original


def test_ordinary_bash_rejection_json_remains_an_ordinary_result(project, tmp_path):
    from tests.runtime_services.tool_results_support import FIXTURES

    chain = Chain(project, tmp_path, 'F08_ORDINARY_REFUSAL')
    scene = scene_for_chain(chain, project, tmp_path)
    select_text(scene)
    configure_chain(chain, scene)
    healthy = wire_case('healthy')
    first = scene.native(healthy['raw'], key='before-ordinary-refusal')['event']
    first['session_id'] = chain.session
    with ParserCalls(prepare_owner()) as prepared:
        chain.invoke(first, event_name='PostToolUse')
        previous = scene.service('full')
        command = shlex.join(['cat', str(FIXTURES / 'native_wire/service-refusal.json')])
        producer = subprocess.run(['bash', '-c', command], cwd=scene.checkout,
                                  env=scene.env, capture_output=True, check=False)
        assert producer.returncode == 0 and producer.stderr == b''
        assert json.loads(producer.stdout) == literal('native_wire/service-refusal.json')
        event = scene.native(producer.stdout.decode(), key='ordinary-refusal-json')['event']
        event['session_id'] = chain.session
        event['tool_input']['command'] = command
        original = deepcopy(event)
        response = chain.invoke(event, event_name='PostToolUse')
        assert chain.delivery()[1]['event']['tool_response'] == producer.stdout.decode()
        assert response['hookSpecificOutput']['additionalContext'] == (
            'Bash: output received; exit unavailable')
        assert response['continue'] is False and event == original
        assert prepared.inputs == [healthy['raw'], producer.stdout.decode()]
        current = scene.service('full')
        assert current['run_id'] != previous['run_id']
        assert Path(current['full_path']).read_bytes() == producer.stdout
