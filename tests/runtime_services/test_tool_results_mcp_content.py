"""Declared real-wire MCP representation, independent literals and real owners.

The project data is sanitized test-owned data shaped like an observed callback.
Fixture native events exercise only isolated owners, never installed host delivery.
"""
from copy import deepcopy
import json
from pathlib import Path
import shlex
from uuid import UUID

import pytest

from poise.modules.result_views.tool_results import (
    ToolResultFailure, ToolResultPolicy, native_snapshot,
)
from tests.hook_transport.architecture_support import Chain, trace
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.session_tool_results_support import (
    OutputCapabilityMissing, ParserCalls, cases, prepare_owner,
)
from tests.runtime_services.tool_results_support import literal


TOOL = 'mcp__fixture__projects'
MARKER = 'F08_ACTUAL_MCP_CONTENT'


def data(name):
    return literal('mcp_content/' + name + '.json')


def policy_for(tmp_path, profile):
    policy = literal('policy.template.json')
    policy.update(literal('session-policy.additions.json'))
    # Pure policy subjects need no project, authority or filesystem arrangement.
    encoded = json.dumps(policy).replace('@RESULTS@', str(tmp_path / 'results'))
    encoded = encoded.replace('@PLACEMENT@', str(tmp_path / 'placement.json'))
    encoded = encoded.replace('@HARNESS@', str(tmp_path / 'state'))
    policy = json.loads(encoded)
    policy['parsers'][TOOL] = deepcopy(profile)
    return policy


def record_for(response):
    return {'tool': {'kind': 'mcp', 'name': TOOL},
            'response': deepcopy(response), 'exit_code': None}


def healthy_pair(response, policy, expected):
    """A real healthy subject must work before malformed/budget assertions count."""
    record = record_for(response)
    original = deepcopy(record)
    trace(MARKER, 'healthy_preparation_enter', record=original, policy=policy)
    with ParserCalls(prepare_owner()) as observed:
        try:
            actual = prepare_owner()(record, {'response': None, 'stdout': None,
                                               'stderr': None}, policy)
        except ToolResultFailure as error:
            trace(MARKER, 'healthy_preparation_refusal', code=error.code,
                  field=error.field, action=error.action, inputs=observed.inputs)
            if error.code == 'parser_failed' and error.field == 'source':
                raise OutputCapabilityMissing(
                    'F08_ACTUAL_MCP_CONTENT_UNSUPPORTED: healthy declared content JSON') from error
            raise
    assert observed.inputs == [response], 'F08_MCP_FULL_PREPARATION_NOT_REACHED'
    assert actual == expected, 'F08_MCP_INDEPENDENT_LITERAL_FORMS'
    assert record == original and policy['parsers'][TOOL]['format'] == 'mcp-content-json'
    trace(MARKER, 'healthy_preparation_return', pair=actual, inputs=observed.inputs)
    return actual


@pytest.mark.parametrize('case', [
    'boolean-index', 'negative-index', 'items-path-type', 'empty-name-path',
    'boolean-version', 'missing-error-template', 'unsafe-template',
])
def test_mcp_content_policy_refuses(tmp_path, case):
    profile = data('profile')
    if case == 'boolean-index':
        profile['content_index'] = True
    elif case == 'negative-index':
        profile['content_index'] = -1
    elif case == 'items-path-type':
        profile['items_path'] = 'projects'
    elif case == 'empty-name-path':
        profile['name_path'] = []
    elif case == 'boolean-version':
        profile['expected_version'] = True
    elif case == 'missing-error-template':
        profile.pop('error_expanded_template')
    else:
        profile['brief_template'] = '{count!r}'
    policy = policy_for(tmp_path, profile)
    original = deepcopy(policy)
    trace(MARKER, 'known_policy_subject_enter', case=case, policy=original)
    with pytest.raises(ToolResultFailure) as refused:
        ToolResultPolicy.parse(policy)
    assert refused.value.code == 'invalid_policy'
    assert policy == original


@pytest.mark.parametrize('case', [
    'actual-wire-shape', 'alternate-declared-paths', 'empty-items',
    'actual-error-text', 'selected-second-block',
])
def test_mcp_content_forms(tmp_path, case):
    profile = data('alternate-profiles').get(case, data('profile'))
    policy = policy_for(tmp_path, profile)
    original = deepcopy(policy)
    assert ToolResultPolicy.parse(policy).data == original
    healthy_pair(data('inputs')[case], policy, data('expected')[case])
    assert policy == original


@pytest.mark.parametrize('case', [
    'invalid-json', 'missing-version', 'version-type', 'version-value', 'items-type',
    'name-type', 'selected-nontext', 'absent-selected-block', 'error-flag-type',
])
def test_mcp_content_invalid_source(tmp_path, case):
    healthy = 'selected-second-block' if case == 'absent-selected-block' else 'actual-wire-shape'
    profile = data('alternate-profiles').get(healthy, data('profile'))
    policy = policy_for(tmp_path, profile)
    healthy_pair(data('inputs')[healthy], policy, data('expected')[healthy])
    record = record_for(data('invalid-inputs')[case])
    original = deepcopy(record)
    trace(MARKER, 'damaged_preparation_enter', case=case, record=original)
    with ParserCalls(prepare_owner()) as observed:
        with pytest.raises(ToolResultFailure) as refused:
            prepare_owner()(record, {'response': None, 'stdout': None, 'stderr': None}, policy)
    actual = {key: getattr(refused.value, key) for key in ('code', 'field', 'action')}
    assert actual == data('errors')['parser']
    assert observed.inputs == [original['response']], 'F08_MCP_DAMAGED_SOURCE_NOT_REACHED'
    assert record == original


@pytest.mark.parametrize('case', ['success', 'error'])
def test_mcp_content_snapshot(tmp_path, case):
    policy = policy_for(tmp_path, data('profile'))
    response = data('inputs')['actual-error-text' if case == 'error' else 'actual-wire-shape']
    event = {'tool_name': TOOL, 'tool_response': response, 'tool_input': {}}
    original = deepcopy(event)
    trace(MARKER, 'native_snapshot_enter', case=case, event=original, policy=policy)
    try:
        actual = native_snapshot(event, policy)
    except ToolResultFailure as error:
        trace(MARKER, 'native_snapshot_refusal', code=error.code, field=error.field)
        if error.code == 'unmapped_input' and error.field == 'format':
            raise OutputCapabilityMissing(
                'F08_ACTUAL_MCP_CONTENT_UNSUPPORTED: declared native snapshot format') from error
        raise
    assert actual['pending'] is False and actual['exit_code'] is None
    assert actual['capture'] == {'state': 'complete', 'reason': None}
    assert actual['tool'] == {'kind': 'mcp', 'name': TOOL}
    assert actual['response'] == response and actual['response'] is not response
    assert event == original


@pytest.mark.parametrize('case', ['brief-chars', 'expanded-utf8'])
def test_mcp_content_budgets(tmp_path, case):
    fixture = data('budgets')[case]
    policy = policy_for(tmp_path, fixture['profile'])
    healthy_pair(fixture['healthy'], policy, fixture['healthy_expected'])
    record = record_for(fixture['response'])
    original = deepcopy(record)
    trace(MARKER, 'budget_preparation_enter', case=case, record=original, policy=policy)
    with ParserCalls(prepare_owner()) as observed:
        with pytest.raises(ToolResultFailure) as refused:
            prepare_owner()(record, {'response': None, 'stdout': None, 'stderr': None}, policy)
    assert {key: getattr(refused.value, key) for key in ('code', 'field', 'action')} == fixture['error']
    assert observed.inputs == [original['response']] and record == original


@pytest.mark.parametrize('case', ['complete', 'error', 'malformed'])
def test_mcp_content_public_session(project, tmp_path, case):
    chain = Chain(project, tmp_path, MARKER)
    scene = scene_for_chain(chain, project, tmp_path)
    scene.policy['parsers'][TOOL] = data('profile')
    scene.save_policy()
    configure_chain(chain, scene, matcher='Bash|mcp__fixture__query|' + TOOL)
    policy_bytes = scene.policy_path.read_bytes()
    if case == 'malformed':
        previous = cases()['mcp']
        event = scene.native(previous['response'], key='healthy-records-predecessor',
                             tool=previous['tool']['name'])['event']
        event['session_id'] = chain.session
        assert chain.invoke(event, event_name='PostToolUse')['hookSpecificOutput'][
            'additionalContext'] == previous['brief']
        predecessor = scene.service('full')
        assert scene.service('expanded')['text'] == previous['expanded']
        before = scene.snapshot()
        response = data('invalid-inputs')['invalid-json']
    else:
        name = 'actual-error-text' if case == 'error' else 'actual-wire-shape'
        response = data('inputs')[name]
    event = scene.native(response, key='actual-mcp-' + case, tool=TOOL)['event']
    event['session_id'] = chain.session
    original = deepcopy(event)
    with ParserCalls(prepare_owner()) as observed:
        actual = chain.invoke(event, event_name='PostToolUse')
    delivery = chain.delivery()[1]
    trace(MARKER, 'actual_public_consumer_return', case=case, event=original,
          delivery=delivery, actual=actual, preparation_inputs=observed.inputs)
    assert delivery['event']['tool_response'] == response and event == original
    assert 'tool-result' in [item['configuration']['handler_id'] for item in delivery['invocations']]
    assert observed.inputs == [response], 'F08_ACTUAL_MCP_CONTENT_UNSUPPORTED: public prepare not reached'
    assert actual['continue'] is False
    text = actual['hookSpecificOutput']['additionalContext']
    if case == 'malformed':
        assert json.loads(text) == data('errors')['native_parser']
        assert scene.snapshot() == before, 'F08_MCP_FAILURE_REPLACED_LAST_OR_PUBLISHED_FILES'
        assert scene.service('full')['run_id'] == predecessor['run_id']
        output = next(item for item in delivery['invocations']
                      if item['configuration']['handler_id'] == 'tool-result')
        assert output['result']['control']['action'] == 'stop'
        assert scene.policy_path.read_bytes() == policy_bytes
        return
    assert text == data('expected')[name]['brief']
    full, expanded = scene.service('full'), scene.service('expanded')
    assert full['run_id'] == expanded['run_id']
    assert str(UUID(full['run_id'])) == full['run_id']
    full_path = Path(full['full_path'])
    assert full_path.parent.name == full['run_id']
    assert json.loads(full_path.read_bytes()) == response
    assert expanded['text'] == data('expected')[name]['expanded']
    saved = json.loads((full_path.parent / scene.policy['storage']['response']).read_bytes())
    assert saved['tool'] == {'kind': 'mcp', 'name': TOOL} and saved['exit_code'] is None
    assert saved['response'] == response
    assert Path(saved['expanded_path']).read_bytes() == data('expected')[name]['expanded'].encode()
    assert json.loads(Path(saved['last_path']).read_bytes()) == {'uuid': full['run_id']}
    code, machine = scene.direct(scene.request('view.template.json',
                                              response_path=saved['response_path']), 'machine')
    assert code == 0 and machine['response'] == response
    before_services = scene.snapshot()
    for operation in ('expanded', 'full'):
        answer = scene.service(operation)
        invocation = scene.calls[-1]
        service = scene.native({'output': invocation['stdout'], 'exit_code': invocation['exit_code']},
                               key='actual-mcp-service-' + operation)['event']
        service['session_id'] = chain.session
        service['tool_input']['command'] = shlex.join(invocation['argv'])
        with ParserCalls(prepare_owner()) as service_calls:
            delivered = chain.invoke(service, event_name='PostToolUse')
        assert service_calls.inputs == [] and scene.snapshot() == before_services
        assert delivered['continue'] is False
        assert delivered['hookSpecificOutput']['additionalContext'] == (
            answer['text'] if operation == 'expanded' else answer['full_path'])
        trace(MARKER, 'actual_associated_service', operation=operation,
              invocation=invocation, answer=answer, delivered=delivered)
    assert scene.policy_path.read_bytes() == policy_bytes
