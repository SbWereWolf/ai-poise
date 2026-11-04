"""Real isolated Poise producers and one declared mixed native pipeline.

No live Codex delivery is claimed. The existing CLI creates the actual
bootstrap/show/release values; helpers never implement profile selection,
source decoding, preparation, storage or hook permission.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from tests.batch.helpers import configure, request
from tests.batch.test_cli import call as poise_cli
from tests.hook_transport.architecture_support import Chain, trace
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.session_tool_results_support import (
    OutputCapabilityMissing, ParserCalls, prepare_owner,
)
from tests.runtime_services.tool_results_support import literal


@pytest.fixture
def real_poise_sources(project):
    """Actual source operations finish before any general-scene transition."""
    configure(project)
    completed = {}

    def invoke(name, packet):
        process = poise_cli(project, packet)
        assert process.returncode == 0 and process.stderr == '', (
            'F08_POISE_PRODUCER_ARRANGEMENT', name, process.returncode,
            process.stdout, process.stderr)
        envelope = json.loads(process.stdout)
        path = Path(envelope['response_path'])
        raw = path.read_bytes()
        body = json.loads(raw)
        command = shlex.join(process.args)
        completed[name] = {'command': command, 'argv': process.args,
                           'response': {'output': process.stdout, 'exit_code': process.returncode},
                           'body': body, 'path': path, 'raw': raw}
        trace('F08_REAL_POISE_SOURCE', 'actual_public_cli_source_complete',
              case=name, argv=process.args, packet=packet, actual_exit_code=process.returncode,
              actual_stdout=process.stdout, actual_stderr=process.stderr,
              source_path=str(path), full_source_text=raw.decode(),
              full_source_bytes=len(raw), full_source_sha256=hashlib.sha256(raw).hexdigest())
        return body

    start = invoke('bootstrap', request('bootstrap', {
        'task': deepcopy(project['task']), 'decision': None,
        'feedback': None, 'rework_stage': None}))
    assert start['status'] == 'active' and start['task'] == 'T1' and start['stage'] == 'tests'
    assert isinstance(start['next_work'], str)
    shown = invoke('show', request('show', {'queries': [{'id': 'state', 'kind': 'task'}]}))
    assert shown['status'] == 'read_only' and len(shown['results']) == 1
    assert 'next_work' not in shown
    release = json.loads((Path(__file__).parents[1] / 'ownership/fixtures/public-release.json').read_text())
    release['input'].update(request_id='profile-producer-release',
                            expected_version=shown['results'][0]['value']['version'],
                            reason='Finish the isolated producer before general output observation.')
    released = invoke('release', release)
    assert released['status'] == 'ownership_released' and 'next_work' not in released
    assert released['after']['task_id'] is released['after']['worktree_task_id'] is None
    for item in completed.values():
        assert item['path'].read_bytes() == item['raw']
    return completed


def profile_scene(project, parent):
    chain = Chain(project, parent, 'F08_REAL_POISE_PROFILE')
    scene = scene_for_chain(chain, project, parent)
    data = literal('poise-profile-policy.json')
    command = shlex.join([sys.executable, '-m', 'poise', 'work'])
    data['selection'][0]['command_pattern'] = re.escape(command)
    data['poise']['source_root'] = str(project['root'])
    scene.policy['selection'] = data['selection']
    scene.policy['parsers']['Poise'].update(data['poise'])
    scene.save_policy()
    assert chain.general.profile['task_adapter'] is None
    return chain, scene


def actual_bash(case):
    data = literal('poise-profile-expected.json')[case]
    process = subprocess.run(data['argv'], capture_output=True, check=False)
    assert process.returncode == data['exit_code']
    assert process.stdout == data['stdout'].encode() and process.stderr == data['stderr'].encode()
    return {'command': shlex.join(process.args),
            'response': {'output': process.stdout.decode(), 'exit_code': process.returncode}}


def selector_owner():
    from poise.modules.result_views import tool_results
    function = getattr(tool_results, 'select_parser', None)
    if not callable(function):
        raise OutputCapabilityMissing('F08_EXPLICIT_PROFILE_SELECTOR_MISSING')
    return function


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'bootstrap', 'show',
                                  'release', 'lookalike', 'ambiguous', 'unmapped'])
def test_explicit_profile_selector(project, tmp_path, real_poise_sources, case):
    from poise.modules.result_views.tool_results import ToolResultFailure
    _, scene = profile_scene(project, tmp_path)
    data = real_poise_sources[case] if case in real_poise_sources else actual_bash('bash-success')
    if case in ('bash-success', 'bash-failure'):
        data = actual_bash(case)
    event = scene.native(data['response'])['event']
    event['tool_input']['command'] = data['command']
    if case == 'lookalike':
        argv = ['bash', '-c', "printf '%s' 'python -m poise work'"]
        process = subprocess.run(argv, capture_output=True, check=False)
        assert process.returncode == 0 and process.stdout == b'python -m poise work' and not process.stderr
        event['tool_input']['command'] = shlex.join(argv)
        event['tool_response'] = {'output': process.stdout.decode(), 'exit_code': 0}
    if case == 'ambiguous':
        duplicate = deepcopy(scene.policy['selection'][1])
        duplicate['id'] = 'another-explicit-bash-rule'
        scene.policy['selection'].append(duplicate)
    if case == 'unmapped':
        event['tool_name'] = 'unconfigured-tool'
    before, original = deepcopy(scene.policy), deepcopy(event)
    choose = selector_owner()
    if case in ('ambiguous', 'unmapped'):
        expected = 'ambiguous_parser' if case == 'ambiguous' else 'unmapped_input'
        with pytest.raises(ToolResultFailure) as refused:
            choose(event, scene.policy)
        assert refused.value.code == expected
    else:
        selected_id, selected = choose(event, scene.policy)
        expected = 'Poise' if case in real_poise_sources else 'Bash'
        assert selected_id == expected, 'F08_ACTUAL_PROFILE_SELECTION'
        assert selected == scene.policy['parsers'][expected]
    assert scene.policy == before and event == original


@pytest.mark.parametrize('case', ['bootstrap', 'show', 'release', 'show-status-only',
                                  'missing-status', 'nonstring-status', 'invalid-count'])
def test_actual_poise_source_forms(project, tmp_path, real_poise_sources, case):
    from poise.modules.result_views.tool_results import ToolResultFailure
    _, scene = profile_scene(project, tmp_path)
    which = 'bootstrap' if case == 'invalid-count' else 'show' if case in (
        'show-status-only', 'missing-status', 'nonstring-status') else case
    body = deepcopy(real_poise_sources[which]['body'])
    if case == 'show-status-only':
        scene.policy['parsers']['Poise'].pop('expanded_fields')
        scene.policy['parsers']['Poise'].pop('expanded_separator')
    elif case == 'missing-status':
        body.pop('status')
    elif case == 'nonstring-status':
        body['status'] = 42
    elif case == 'invalid-count':
        body['results'] = 'not-an-array'
    record = {'tool': {'kind': 'generic', 'name': 'Poise'}, 'response': body, 'exit_code': 0}
    sources = {'stdout': None, 'stderr': None, 'response': None}
    if case in ('missing-status', 'nonstring-status', 'invalid-count'):
        with pytest.raises(ToolResultFailure) as refused:
            prepare_owner()(record, sources, scene.policy)
        assert refused.value.code == 'parser_failed'
    else:
        expected = literal('poise-profile-expected.json')[case]
        with ParserCalls(prepare_owner()) as calls:
            pair = prepare_owner()(record, sources, scene.policy)
        assert calls.inputs == [body], 'F08_REAL_POISE_PRODUCER_NOT_REACHED'
        assert pair == expected, 'F08_REAL_POISE_SOURCE_FORMS'
    assert real_poise_sources[which]['path'].read_bytes() == real_poise_sources[which]['raw']


@pytest.mark.parametrize('order', ['bash-first', 'poise-first'])
def test_one_policy_mixed_native_outputs(project, tmp_path, real_poise_sources, order):
    chain, scene = profile_scene(project, tmp_path)
    configure_chain(chain, scene)
    data = {**real_poise_sources, 'bash-success': actual_bash('bash-success'),
            'bash-failure': actual_bash('bash-failure')}
    expected = literal('poise-profile-expected.json')
    sequence = (['bash-success', 'bootstrap', 'bash-failure', 'show', 'release']
                if order == 'bash-first' else
                ['show', 'bash-failure', 'release', 'bash-success', 'bootstrap'])
    policy_bytes, namespace, ids = scene.policy_path.read_bytes(), scene.policy['session']['root'], set()
    installation = deepcopy(chain.installation)
    for index, name in enumerate(sequence):
        source = data[name]
        event = scene.native(source['response'], key='mixed-' + str(index))['event']
        event['session_id'] = chain.session
        event['tool_input']['command'] = source['command']
        original = deepcopy(event)
        with ParserCalls(prepare_owner()) as calls:
            delivered = chain.invoke(event, event_name='PostToolUse')
        _, delivery = chain.delivery()
        assert delivery['event']['tool_response'] == source['response'] and event == original
        trace('F08_REAL_POISE_MIX', 'actual_mixed_delivery', case=name, order=order,
              native_input=original, delivery=delivery, actual_return=delivered)
        assert delivered.get('continue') is not False
        assert delivered['hookSpecificOutput']['additionalContext'] == expected[name]['brief'], (
            'F08_MIXED_POISE_SOURCE_NOT_SELECTED', name, delivered)
        actual_source = source['body'] if name in real_poise_sources else source['response']
        assert calls.inputs == [actual_source], 'F08_MIXED_PARSER_DID_NOT_RECEIVE_FULL_SOURCE'
        full = scene.service('full')
        expanded = scene.service('expanded')
        assert expanded['run_id'] == full['run_id'] and full['run_id'] not in ids
        ids.add(full['run_id'])
        assert expanded['text'] == expected[name]['expanded']
        assert json.loads(Path(full['full_path']).read_bytes()) == actual_source
        if name in real_poise_sources:
            assert Path(full['full_path']).read_bytes() == source['raw']
            assert source['path'].read_bytes() == source['raw']
        folder = Path(full['full_path']).parent
        saved = json.loads((folder / scene.policy['storage']['response']).read_bytes())
        assert saved['tool']['name'] == 'Bash' and saved['exit_code'] == source['response']['exit_code']
        assert json.loads(Path(saved['last_path']).read_bytes()) == {'uuid': full['run_id']}
        assert Path(saved['expanded_path']).read_bytes() == expected[name]['expanded'].encode()
        assert scene.policy_path.read_bytes() == policy_bytes and scene.policy['session']['root'] == namespace
        assert chain.installation == installation
    assert len(ids) == 5


def test_selection_policy_keeps_actual_service_delivery(project, tmp_path):
    chain, scene = profile_scene(project, tmp_path)
    configure_chain(chain, scene)
    source = actual_bash('bash-success')
    event = scene.native(source['response'], key='before-service')['event']
    event['session_id'] = chain.session
    event['tool_input']['command'] = source['command']
    assert chain.invoke(event, event_name='PostToolUse').get('continue') is not False
    before = scene.snapshot()
    for operation in ('expanded', 'full'):
        answer = scene.service(operation)
        invocation = scene.calls[-1]
        service = scene.native({'output': invocation['stdout'], 'exit_code': invocation['exit_code']},
                               key='service-' + operation)['event']
        service['session_id'] = chain.session
        service['tool_input']['command'] = shlex.join(invocation['argv'])
        with ParserCalls(prepare_owner()) as calls:
            delivered = chain.invoke(service, event_name='PostToolUse')
        assert calls.inputs == [] and scene.snapshot() == before
        assert delivered.get('continue') is not False
        assert delivered['hookSpecificOutput']['additionalContext'] == (
            answer['text'] if operation == 'expanded' else answer['full_path'])
        assert chain.delivery()[1]['event']['tool_response'] == service['tool_response']


@pytest.mark.parametrize('case', ['invalid-regex', 'missing-profile', 'duplicate-id'])
def test_known_selection_configuration_refuses(project, tmp_path, case):
    from poise.modules.result_views.tool_results import ToolResultFailure, ToolResultPolicy
    _, scene = profile_scene(project, tmp_path)
    if case == 'invalid-regex':
        scene.policy['selection'][0]['command_pattern'] = '['
    elif case == 'missing-profile':
        scene.policy['selection'][0]['profile'] = 'not-configured'
    else:
        scene.policy['selection'][1]['id'] = scene.policy['selection'][0]['id']
    scene.policy['unrelated_operator_option'] = {'preserve': [0, False, None]}
    before = deepcopy(scene.policy)
    with pytest.raises(ToolResultFailure) as refused:
        ToolResultPolicy.parse(scene.policy)
    assert refused.value.code == 'invalid_policy'
    assert scene.policy == before
