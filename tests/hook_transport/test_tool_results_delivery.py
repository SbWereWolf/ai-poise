"""H isolated: existing native owners, actual ordered chain and read ledger.

Fixture events exercise test-owned public profiles; no installed Codex host
delivery or machine Promise is claimed by these tests.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from tests.hook_transport.architecture_support import Chain, WorkerBarrier, write
from tests.hook_transport.session_output_support import (
    configure_chain, install_output, output_handler, scene_for_chain,
)
from tests.runtime_services.session_tool_results_support import cases


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'mcp'])
def test_isolated_post_tool_use_delivers_prepared_brief_and_full_source(project, tmp_path, case):
    chain = Chain(project, tmp_path, 'F08_ISOLATED_OUTPUT')
    scene = scene_for_chain(chain, project, tmp_path)
    data = cases()[case]
    matcher = 'Bash|mcp__fixture__query' if case == 'mcp' else 'Bash'
    configure_chain(chain, scene, matcher=matcher)
    event = scene.native(data['response'], tool=data['tool']['name'])['event']
    event['session_id'] = chain.session
    original = deepcopy(event)
    result = chain.invoke(event, event_name='PostToolUse')
    assert event == original, 'F08_HOOK_CHANGED_ORIGINAL_EVENT'
    assert result['continue'] is False
    assert result['hookSpecificOutput'] == {'hookEventName': 'PostToolUse',
                                            'additionalContext': data['brief']}
    assert 'FULL_ONLY_F08' not in json.dumps(result)
    full = scene.service('full')
    assert json.loads(Path(full['full_path']).read_bytes()) == data['response']
    expanded = scene.service('expanded')
    assert expanded['run_id'] == full['run_id'] and expanded['text'] == data['expanded']
    _, receipt = chain.delivery()
    assert receipt['event']['tool_use_id'] == original['tool_use_id']
    assert receipt['event_receipt']['event'] == 'PostToolUse'


@pytest.mark.parametrize('mode', ['next-sync', 'async-before'])
def test_output_joins_existing_sync_async_chain(project, tmp_path, mode):
    chain = Chain(project, tmp_path, 'F08_OUTPUT_EXISTING_CHAIN')
    scene = scene_for_chain(chain, project, tmp_path)
    data = cases()['bash-success']
    with WorkerBarrier() as barrier:
        before = ([chain.command('worker', mode='async', barrier=barrier.address)]
                  if mode == 'async-before' else [])
        after = [chain.command('after')]
        configure_chain(chain, scene, before=before, after=after)
        event = scene.native(data['response'])['event']
        event['session_id'] = chain.session
        result = chain.invoke(event, event_name='PostToolUse')
        if before:
            assert barrier.arrived(1) == {'worker'}
            packets = chain.inputs()
            assert sorted(row['configuration']['handler_id'] for row in packets) == ['after', 'worker']
            assert [row['configuration']['handler_id'] for row in chain.delivery()[1]['invocations']] == [
                'worker', 'tool-result', 'after']
        else:
            packets = chain.assert_order(['after'])
        observer = next(row for row in packets if row['configuration']['handler_id'] == 'after')
        assert observer['response']['hookSpecificOutput']['additionalContext'] == data['brief']
        assert result['hookSpecificOutput']['additionalContext'] == data['brief']
        if before:
            barrier.release('worker')
            worker, = chain.terminal_workers(1)
            chain.assert_complete_worker_capture(worker)
            assert chain.delivery()[1]['response'] == result


def test_ordinary_poise_saved_response_enters_same_hook_path(project, tmp_path):
    import io
    from poise.interfaces.work import write_result
    from tests.runtime_services.test_output_boundary_agent import AgentBoundaryTests
    chain = Chain(project, tmp_path, 'F08_ORDINARY_POISE_OUTPUT')
    scene = scene_for_chain(chain, project, tmp_path)
    # Reuse the existing real Work-presentation fixture, not a fake work action.
    producer = AgentBoundaryTests()
    producer.setUp()
    try:
        producer.runtime.session = chain.session
        source = deepcopy(cases()['poise']['response'])
        source['result']['unselected'] = 'FULL_ONLY_F08' * 1000
        output = io.StringIO()
        write_result(producer.runtime, source, output)
        envelope = json.loads(output.getvalue())
        saved = Path(envelope['response_path'])
        assert saved.is_file() and json.loads(saved.read_bytes()) == source
        assert envelope != source and envelope['session'] == chain.session
        scene.policy['parsers']['Bash'] = deepcopy(scene.policy['parsers']['Poise'])
        scene.policy['parsers']['Bash'].update(format='saved-work-json',
                                              source_root=str(producer.root))
        scene.save_policy()
        configure_chain(chain, scene)
        native_response = {'output': output.getvalue(), 'exit_code': 0}
        event = scene.native(native_response)['event']
        event['session_id'] = chain.session
        result = chain.invoke(event, event_name='PostToolUse')
        assert result['continue'] is False
        assert result['hookSpecificOutput']['additionalContext'] == 'Poise: verified'
        full = scene.service('full')
        assert json.loads(Path(full['full_path']).read_bytes()) == source
        assert chain.delivery()[1]['event']['tool_response'] == native_response
        assert scene.service('expanded')['text'] == 'Poise: verified\nNext: inspect tests'
    finally:
        producer.doCleanups()


def test_output_keeps_real_bash_intake_coverage(project, tmp_path):
    chain = Chain(project, tmp_path, 'F08_OUTPUT_INTAKE')
    scene = scene_for_chain(chain, project, tmp_path)
    definition, events, inputs = chain.general.common_builtin_route()
    output = write(tmp_path / 'output-with-intake.json', {
        'schema': 'hook-handler-configuration-1', 'id': 'output-with-intake',
        'handlers': [output_handler(scene.policy_path)]})
    definition['routes']['PostToolUse'].append(str(output))
    installed = install_output(chain.service, definition, 'f08-real-intake-with-output')
    start = next(row for row in events if row['hook_event_name'] == 'SessionStart')
    chain.service.event(installed['definition_path'], start)
    before = next(row for row in events if row['hook_event_name'] == 'PreToolUse')
    pre = chain.service.event(installed['definition_path'], before)
    assert pre.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    command = before['tool_input']['command']
    cp = subprocess.run(['bash', '-c', command], cwd=project['app'],
                        capture_output=True, check=False)
    assert cp.returncode == 0 and cp.stdout == b'A01\nA02\n' and cp.stderr == b''
    post = deepcopy(before)
    post.update(hook_event_name='PostToolUse',
                tool_response={'output': cp.stdout.decode(), 'exit_code': cp.returncode})
    result = chain.service.event(installed['definition_path'], post)
    assert result['continue'] is False
    environment = dict(chain.general.env)
    environment.pop('POISE_CALLER_BINDING')
    environment['CODEX_THREAD_ID'] = start['session_id']
    observed = chain.general.invoke('reader', packet={'operation': 'coverage'},
                                    policy=inputs['reader_policy'], environment=environment)
    coverage = json.loads(observed['stdout'])
    document, = [row for row in coverage['details']['documents']
                 if row['path'] == 'docs/sample.txt']
    assert document['ranges'] == [[1, 2]]
    assert coverage['details']['counts']['pending'] == 0
    assert observed['sentinel']['task_imports'] == []
