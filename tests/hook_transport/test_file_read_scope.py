"""File data stays with its reader; ordinary output uses the common formatter.

Public readers and public native dispatcher run in one declared isolated episode.
No fixture callback is claimed to be an actual desktop/model-input observation.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import shlex

import pytest

from tests.content_reader.helpers import Scenario as ReaderScenario, FIXTURES as READER_FIXTURES
from tests.hook_transport.architecture_support import Chain
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.native_wire_support import select_text
from tests.runtime_services.session_tool_results_support import ParserCalls, SessionScenario, prepare_owner


READER_COMMAND = 'fixture-reader --all docs/file.txt'
ORDINARY_BRIEF = 'Bash: output received; exit unavailable'


def rules(command, *, priority=20, file_read=True):
    return [
        {'id': 'file-reader', 'tool_name': 'Bash', 'profile': 'Bash',
         'priority': priority, 'command_pattern': re.escape(command), 'file_read': file_read},
        {'id': 'ordinary', 'tool_name': 'Bash', 'profile': 'Bash',
         'priority': 10, 'command_pattern': None, 'file_read': False},
    ]


def output_scene(project, tmp_path):
    chain = Chain(project, tmp_path, 'FILE_READ_SCOPE')
    output = scene_for_chain(chain, project, tmp_path)
    select_text(output)
    output.policy['display'].update(output_chars=1800, output_bytes=2400)
    return chain, output


def native_event(chain, output, body, command, key='file-result'):
    event = output.native(body, key=key, tool='Bash')['event']
    event['session_id'] = chain.session
    event['tool_input']['command'] = command
    return event


def setup_reader_return(project, tmp_path, selection):
    chain, output = output_scene(project, tmp_path)
    parent = tmp_path / 'reader-client'
    parent.mkdir()
    selected = {**project, 'cfg': chain.general.profile, 'config_path': chain.general.path}
    reader = ReaderScenario(selected, parent)
    if selection == 'complete':
        raw = (READER_FIXTURES / 'long-body.txt').read_bytes()
        expected = raw.decode('utf-8')
        assert len(raw) > output.policy['display']['output_bytes']
        path = 'src/large.py'
        request = reader.request(path=path, complete=True)
    else:
        raw = b'first\r\nsecond\r\nthird-without-newline'
        expected = 'second\r\nthird-without-newline'
        path = 'tests/range.py'
        request = reader.request(path=path, start=2, end=3)
    reader.write(path, raw)
    command = shlex.join(reader.argv('text')) + ' <<< ' + shlex.quote(
        json.dumps(request, ensure_ascii=False, separators=(',', ':')))
    output.policy['selection'] = rules(command)
    output.save_policy()
    configure_chain(chain, output, include_end=True)
    # Reuse the actual public Start and binding; never create a second/fake episode.
    binding = chain.service.latest_binding(chain.session, 'primary')
    _, start = chain.delivery()
    reader.native_identity(chain.session)
    reader.hooks = chain.service
    reader.installation = chain.installation
    reader.episode = {
        'project_id': reader.project, 'session_id': chain.session,
        'binding_id': binding['session_id'],
        'start_event_id': start['event_receipt']['native_start_event_id'],
    }
    reader.data['admission'] = {
        'hook_settings': str(chain.service.settings.path),
        'definition_path': chain.installation['definition_path'],
    }
    reader.save_policy()
    reader.ready()
    code, body, stderr = reader.raw(request, fmt='text')
    assert code == 0 and not stderr, 'FILE_READER_SETUP_PUBLIC_READ_FAILED'
    header_text, separator, content = body.partition('\n')
    header = json.loads(header_text)
    assert separator and header['schema'] == 'poise-content-read-result-1'
    assert header['status'] == 'ready' and header['item']['content_follows'] is True
    assert content == expected, 'EXISTING_READER_DID_NOT_RETURN_REQUESTED_DATA'
    assert header['item']['content_bytes'] == len(expected.encode())
    assert reader.episode == header['context']['native_episode']
    return chain, output, body, command, expected


@pytest.mark.parametrize('selection', ['complete', 'range'])
def test_actual_file_reader_return_is_outside_ordinary_output_shortening(project, tmp_path, selection):
    chain, output, body, command, expected = setup_reader_return(project, tmp_path, selection)
    event = native_event(chain, output, body, command)
    original = deepcopy(event)
    before = output.snapshot()
    with ParserCalls(prepare_owner()) as prepared:
        response = chain.invoke(event, event_name='PostToolUse')
    assert response.get('continue') is not False, 'FILE_READ_WAS_REPLACED_AS_TOOL_OUTPUT'
    assert 'additionalContext' not in response.get('hookSpecificOutput', {}), 'FILE_READ_WAS_SUMMARIZED'
    assert prepared.inputs == [], 'FILE_READ_ENTERED_OUTPUT_PARSER'
    assert output.snapshot() == before, 'FILE_READ_CREATED_DUPLICATE_TOOL_CAPTURE'
    _, delivery = chain.delivery()
    assert delivery['event']['tool_response'] == body and event == original
    assert delivery['event_receipt']['event'] == 'PostToolUse'
    assert body.encode().endswith(expected.encode())
    if selection == 'range':
        assert '\r\n' in expected and not expected.endswith('\n')


def test_same_reader_payload_on_an_ordinary_command_is_observed_and_saved(project, tmp_path):
    chain, output, body, _, _ = setup_reader_return(project, tmp_path, 'complete')
    event = native_event(chain, output, body, 'fixture ordinary', key='ordinary-file-shaped-output')
    response = chain.invoke(event, event_name='PostToolUse')
    assert response.get('continue') is not False
    assert response['hookSpecificOutput']['additionalContext'] == ORDINARY_BRIEF
    saved = output.service('full')
    assert Path(saved['full_path']).read_bytes() == body.encode()
    _, delivery = chain.delivery()
    assert delivery['event']['tool_response'] == body


@pytest.mark.parametrize('priority,ambiguous', [(5, False), (10, True)])
def test_file_read_scope_uses_the_existing_winning_rule(project, tmp_path, priority, ambiguous):
    chain, output = output_scene(project, tmp_path)
    output.policy['selection'] = rules(READER_COMMAND, priority=priority)
    output.save_policy()
    configure_chain(chain, output)
    event = native_event(chain, output, 'file contents\n', READER_COMMAND)
    response = chain.invoke(event, event_name='PostToolUse')
    assert response.get('continue') is not False
    text = response['hookSpecificOutput']['additionalContext']
    if ambiguous:
        value = json.loads(text)
        assert value['status'] == 'rejected' and value['error']['code'] == 'ambiguous_parser'
    else:
        assert text == ORDINARY_BRIEF


@pytest.mark.parametrize('after_action', ['continue', 'stop'])
def test_file_scope_keeps_foreign_handler_order_context_and_control(project, tmp_path, after_action):
    chain, output = output_scene(project, tmp_path)
    output.policy['selection'] = rules(READER_COMMAND)
    output.save_policy()
    configure_chain(chain, output, before=[chain.command('before')],
                    after=[chain.command('after', action=after_action), chain.command('tail')])
    event = native_event(chain, output, 'file contents\r\nlast', READER_COMMAND)
    response = chain.invoke(event, event_name='PostToolUse')
    assert 'additionalContext' not in response.get('hookSpecificOutput', {}), 'FILE_SCOPE_ADDED_OUTPUT_REPLACEMENT'
    labels = ['before', 'after'] if after_action == 'stop' else ['before', 'after', 'tail']
    assert response['systemMessage'] == 'seed|' + '|'.join(labels)
    assert [row['label'] for row in chain.order()] == labels
    assert response.get('continue') is not False
    _, delivery = chain.delivery()
    after = next(row for row in delivery['invocations']
                 if row['configuration']['handler_id'] == 'after')
    assert after['result']['control'] == {
        'action': after_action, 'reason': None if after_action == 'continue' else 'fixture stop'}
    assert delivery['event']['tool_response'] == event['tool_response']


def test_file_read_scope_flag_is_a_strict_boolean_in_public_policy(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    scene.policy['selection'] = rules(READER_COMMAND, file_read='true')
    scene.save_policy()
    code, stdout, stderr = scene.invoke(scene.input(), 'agent')
    assert not stderr
    assert code == 2, 'FILE_READ_SCOPE_FLAG_WAS_NOT_VALIDATED'
    value = json.loads(stdout)
    assert value['status'] == 'rejected' and value['error']['code'] == 'invalid_policy'
