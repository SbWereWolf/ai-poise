"""H isolated: existing native owners, actual ordered chain and read ledger.

Fixture events exercise test-owned public profiles; no installed Codex host
delivery or machine Promise is claimed by these tests.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import shlex

import pytest

from tests.hook_transport.architecture_support import Chain, WorkerBarrier, write
from tests.hook_transport.session_output_support import (
    configure_chain, output_handler, scene_for_chain,
)
from tests.runtime_services.session_tool_results_support import ParserCalls, cases, prepare_owner


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'mcp', 'unicode-nul'])
def test_enforce_serialized_feedback_contains_only_brief(project, tmp_path, case):
    from poise.interfaces.hook_transport import _write_native
    from tests.runtime_services.native_wire_support import select_text, wire_case
    chain = Chain(project, tmp_path, 'ENFORCE_BRIEF_ONLY')
    scene = scene_for_chain(chain, project, tmp_path)
    scene.policy['hook']['mode'] = 'enforce'
    scene.save_policy()
    if case == 'unicode-nul':
        select_text(scene)
        data = wire_case(case)
        source, brief, expanded, tool = data['raw'], data['brief'], data['expanded'], 'Bash'
    else:
        data = cases()[case]
        source, brief, expanded = data['response'], data['brief'], data['expanded']
        tool = data['tool']['name']
    source = deepcopy(source)
    if isinstance(source, str):
        source += 'FULL_ONLY_PADDING\n' * 256
    else:
        source['full_only_padding'] = 'FULL_ONLY_PADDING\n' * 256
    configure_chain(chain, scene, matcher='Bash|mcp__fixture__query',
                    after=[chain.command('after')])
    event = scene.native(source, tool=tool)['event']
    event['session_id'] = chain.session
    event['transcript_path'] = None
    original = deepcopy(event)
    with ParserCalls(prepare_owner()) as calls:
        response = chain.invoke(event, event_name='PostToolUse')
        output = io.StringIO()
        _write_native(chain.service, response, chain.installation['definition_path'], output)
        wire = output.getvalue()
        actual = json.loads(wire)
        full = scene.service('full')
        before = scene.snapshot()
        detail = scene.service('expanded')
        again = scene.service('full')
    assert len(calls.inputs) == 1, 'ENFORCE_REPLAYED_PRODUCER_OR_RECAPTURED_VIEW'
    assert detail['text'] == expanded and detail['run_id'] == full['run_id'] == again['run_id']
    assert scene.snapshot() == before, 'ENFORCE_VIEW_CHANGED_ORIGINAL_RESULT'
    saved = Path(full['full_path']).read_bytes()
    assert (saved == source.encode() if isinstance(source, str) else json.loads(saved) == source)
    assert event == original and chain.delivery()[1]['event'] == original
    assert [row['label'] for row in chain.order()] == ['after']
    assert actual.get('continue') is False, 'ENFORCE_FEEDBACK_NOT_REPLACING_RAW_RESULT'
    assert actual['stopReason'] == brief, 'ENFORCE_FEEDBACK_LOST_USEFUL_BRIEF'
    assert actual.get('hookSpecificOutput', {}).get('additionalContext', '') in ('', brief)
    assert 'FULL_ONLY' not in wire and str(scene.root) not in wire
    assert full['full_path'] not in wire and 'response_path' not in wire
    assert len(wire) <= scene.policy['display']['output_chars']
    assert len(wire.encode()) <= scene.policy['display']['output_bytes']


def test_enforce_native_budget_counts_escaped_feedback_and_framing(project, tmp_path):
    from poise.infrastructure.hook_transport import HookService
    from poise.interfaces.hook_transport import _write_native
    chain = Chain(project, tmp_path, 'ENFORCE_NATIVE_WIRE_BUDGET')
    scene = scene_for_chain(chain, project, tmp_path)
    scene.policy['hook']['mode'] = 'enforce'
    scene.policy['display'].update(output_chars=10000, output_bytes=20000)
    scene.policy['parsers']['Bash']['brief'].update(chars=512, bytes=1024)
    scene.save_policy()
    configure_chain(chain, scene, after=[chain.command('after')])
    # Test-owned native transport limit is tighter than the prepared-view limit.
    # A correct brief envelope can fit the latter and still overflow the former.
    settings = json.loads(chain.general.settings_path.read_bytes())
    settings['output_chars'] = 500
    write(chain.general.settings_path, settings)
    chain.service = HookService(chain.general.settings_path)
    brief = 'Bash: exit 7; ' + '"\\' * 150
    mandatory = json.dumps({'continue': False, 'stopReason': brief},
                           ensure_ascii=False, separators=(',', ':')) + '\n'
    assert len(brief) <= 500 < len(mandatory)
    source = deepcopy(cases()['bash-failure']['response'])
    source['output'] = 'RESULT: ' + '"\\' * 150 + '\n'
    event = scene.native(source)['event']
    event['session_id'] = chain.session
    original = deepcopy(event)
    response = chain.invoke(event, event_name='PostToolUse')
    output = io.StringIO()
    _write_native(chain.service, response, chain.installation['definition_path'], output)
    wire = output.getvalue()
    actual = json.loads(wire)
    assert event == original and chain.delivery()[1]['event']['tool_response'] == source
    full = scene.service('full')
    assert json.loads(Path(full['full_path']).read_bytes()) == source
    assert [row['label'] for row in chain.order()] == ['after']
    assert wire.endswith('\n') and len(wire) <= 500
    assert len(wire.encode()) <= scene.policy['display']['output_bytes']
    assert actual.get('continue') is False, 'ENFORCE_NATIVE_BUDGET_HAS_NO_BOUNDED_REPLACEMENT'
    refusal = json.loads(actual['stopReason'])
    assert refusal['status'] == 'rejected' and refusal['exit_code'] == 7
    assert refusal['error']['code'] == 'budget_too_small'
    assert str(scene.root) not in wire and 'response_path' not in wire


def test_enforce_projection_failure_preserves_producer_failure(project, tmp_path):
    from poise.interfaces.hook_transport import _write_native
    chain = Chain(project, tmp_path, 'ENFORCE_PROJECTION_REFUSAL')
    scene = scene_for_chain(chain, project, tmp_path)
    scene.policy['hook']['mode'] = 'enforce'
    scene.save_policy()
    configure_chain(chain, scene, after=[chain.command('after')])
    source = deepcopy(cases()['bash-failure']['response'])
    source['output'] = 'RESULT: ' + 'x' * 129
    event = scene.native(source)['event']
    event['session_id'] = chain.session
    response = chain.invoke(event, event_name='PostToolUse')
    output = io.StringIO()
    _write_native(chain.service, response, chain.installation['definition_path'], output)
    wire = output.getvalue()
    actual = json.loads(wire)
    assert chain.delivery()[1]['event']['tool_response'] == source
    assert [row['label'] for row in chain.order()] == ['after']
    assert actual.get('continue') is False, 'ENFORCE_REFUSAL_LEAVES_RAW_RESULT_VISIBLE'
    refusal = json.loads(actual['stopReason'])
    assert refusal['status'] == 'rejected' and refusal['exit_code'] == 7
    assert refusal['error']['code'] == 'budget_too_small'
    assert str(scene.root) not in wire and 'response_path' not in wire


@pytest.mark.parametrize('case', ['parser', 'budget', 'source', 'healthy'])
def test_output_rejection_crosses_real_hook_boundary(project, tmp_path, case):
    from tests.runtime_services.tool_results_support import literal
    chain = Chain(project, tmp_path, 'F08_HOOK_TYPED_REJECTION')
    scene = scene_for_chain(chain, project, tmp_path)
    data = literal('code-hook-rejections.json')[case]
    if case == 'budget':
        scene.policy['parsers']['mcp__fixture__query']['expanded']['chars'] = data['expanded_chars']
        scene.save_policy()
    configure_chain(chain, scene, matcher='Bash|mcp__fixture__query',
                    before=[chain.command('before')], after=[chain.command('after')])
    healthy = cases()['mcp']
    predecessor = scene.native(healthy['response'], key='healthy-before-error', tool=healthy['tool']['name'])['event']
    predecessor['session_id'] = chain.session
    first = chain.invoke(predecessor, event_name='PostToolUse')
    assert first.get('continue') is not False and first['hookSpecificOutput']['additionalContext'] == healthy['brief']
    previous = scene.service('full')
    assert scene.service('expanded')['text'] == healthy['expanded']
    before = scene.snapshot()
    event = scene.native(healthy['response'], key='typed-rejection-' + case, tool=healthy['tool']['name'])['event']
    event['session_id'] = chain.session
    if case == 'parser':
        event['tool_response']['structuredContent'].pop('count')
    elif case == 'budget':
        event['tool_response']['structuredContent']['names'] = data['names']
    elif case == 'source':
        event.pop('tool_response')
    original = deepcopy(event)
    with ParserCalls(prepare_owner()) as calls:
        # Exact public input: Chain.invoke merges default fields and would
        # accidentally replace the deliberately missing tool_response.
        actual = chain.service.event(chain.installation['definition_path'], event)
    binding = chain.service.latest_binding(chain.session, 'primary')
    with chain.service.registry.read_transaction() as db:
        chain.last_event_id = db.execute(
            'SELECT max(id) FROM hook_events WHERE binding=? AND event=?',
            (binding['session_id'], 'PostToolUse')).fetchone()[0]
    chain.last_event_name = 'PostToolUse'
    _, delivery = chain.delivery()
    assert delivery['event'] == original and event == original
    assert 'tool-result' in [row['configuration']['handler_id'] for row in delivery['invocations']]
    assert len(calls.inputs) == (0 if case == 'source' else 1), 'F08_ERROR_SUBJECT_NOT_REACHED'
    print(json.dumps({'boundary': 'actual-typed-hook-rejection', 'case': case,
                      'event': original, 'actual': actual, 'delivery': delivery}, ensure_ascii=False))
    assert actual.get('continue') is not False, 'F08_HOOK_ERROR_NOT_DELIVERED'
    text = actual['hookSpecificOutput']['additionalContext']
    assert len(text) <= scene.policy['display']['output_chars']
    assert len(text.encode()) <= scene.policy['display']['output_bytes']
    if case == 'healthy':
        assert text == healthy['brief']
        assert scene.service('expanded')['text'] == healthy['expanded']
        assert scene.service('full')['run_id'] != previous['run_id']
        assert [row['label'] for row in chain.order()] == ['before', 'after', 'before', 'after']
    else:
        assert json.loads(text) == data['error'], 'F08_HOOK_TYPED_REJECTION_FIELDS'
        assert 'FULL_ONLY_F08' not in text
        assert scene.snapshot() == before, 'F08_HOOK_FAILURE_PUBLISHED_OR_REPLACED_LAST'
        assert scene.service('full')['run_id'] == previous['run_id']
        assert [row['label'] for row in chain.order()] == ['before', 'after', 'before', 'after']
        output = next(row for row in delivery['invocations']
                      if row['configuration']['handler_id'] == 'tool-result')
        assert output['result']['control']['action'] == 'continue'
        assert output['result']['control']['reason'] is None


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
    assert result.get('continue') is not False
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
        assert result.get('continue') is not False
        assert result['hookSpecificOutput']['additionalContext'] == 'Poise: verified'
        full = scene.service('full')
        assert json.loads(Path(full['full_path']).read_bytes()) == source
        assert chain.delivery()[1]['event']['tool_response'] == native_response
        assert scene.service('expanded')['text'] == 'Poise: verified\nNext: inspect tests'
    finally:
        producer.doCleanups()


@pytest.mark.parametrize('operation', ['expanded', 'full'])
def test_actual_service_result_crosses_hook_without_capture(project, tmp_path, operation):
    from poise.interfaces.work import write_result
    from tests.runtime_services.test_output_boundary_agent import AgentBoundaryTests
    chain = Chain(project, tmp_path, 'F08_SERVICE_DELIVERY_EXCLUSION')
    scene = scene_for_chain(chain, project, tmp_path)
    producer = AgentBoundaryTests()
    producer.setUp()
    try:
        producer.runtime.session = chain.session
        scene.policy['parsers']['Bash'] = deepcopy(scene.policy['parsers']['Poise'])
        scene.policy['parsers']['Bash'].update(format='saved-work-json',
                                              source_root=str(producer.root))
        scene.save_policy()
        configure_chain(chain, scene)

        def ordinary_response(key):
            source = deepcopy(cases()['poise']['response'])
            source['result']['unselected'] = 'FULL_ONLY_F08:' + key
            output = io.StringIO()
            write_result(producer.runtime, source, output)
            actual = json.loads(output.getvalue())
            assert json.loads(Path(actual['response_path']).read_bytes()) == source
            request = scene.native({'output': output.getvalue(), 'exit_code': 0}, key=key)
            event = request['event']
            event['session_id'] = chain.session
            # Ordinary Poise remains captureable even with these words present.
            event['tool_input']['command'] = 'python -m poise work # expanded full'
            return event, source

        with ParserCalls(prepare_owner()) as calls:
            first_event, first_source = ordinary_response('ordinary-before-service')
            first_result = chain.invoke(first_event, event_name='PostToolUse')
            assert first_result['hookSpecificOutput']['additionalContext'] == 'Poise: verified'
            full_before = scene.service('full')
            run_id = full_before['run_id']
            full_path = Path(full_before['full_path'])
            assert json.loads(full_path.read_bytes()) == first_source
            session_root = Path(scene.policy['session']['root'])
            run_files = {str(path): path.read_bytes() for path in full_path.parent.iterdir()
                         if path.is_file()}
            last_paths = list(session_root.rglob(scene.policy['session']['last']))
            assert len(last_paths) == 1
            last = last_paths[0]
            last_before = last.read_bytes()
            full_paths_before = set(session_root.rglob(scene.policy['session']['full']))
            assert len(calls.inputs) == 1 and calls.inputs == [first_source]

            # Real public CLI result, including metadata issued by its owner.
            # The fixture neither inserts an exemption flag nor returns content.
            answer = scene.service(operation)
            expected_metadata = {'purpose': 'harness-service', 'capability': 'tool-result'}
            assert {key: answer.get(key) for key in expected_metadata} == expected_metadata, (
                'F08_SERVICE_PURPOSE_CAPABILITY_MISSING', answer)
            actual_call = scene.calls[-1]
            assert actual_call['exit_code'] == 0 and actual_call['stderr'] == ''
            assert json.loads(actual_call['stdout']) == answer and answer['run_id'] == run_id
            service_event = scene.native({'output': actual_call['stdout'], 'exit_code': 0},
                                         key='actual-service-' + operation)['event']
            service_event['session_id'] = chain.session
            service_event['tool_input']['command'] = shlex.join(actual_call['argv'])
            original = deepcopy(service_event)
            delivered = chain.invoke(service_event, event_name='PostToolUse')
            assert service_event == original
            assert chain.delivery()[1]['event']['tool_response'] == original['tool_response']
            expected = answer['text'] if operation == 'expanded' else answer['full_path']
            assert delivered.get('continue') is not False
            assert delivered['hookSpecificOutput']['additionalContext'] == expected, (
                'F08_SERVICE_DELIVERY_CHANGED_SAVED_CONTENT', delivered)
            assert last.read_bytes() == last_before, 'F08_SERVICE_HOOK_REPLACED_LAST'
            assert set(session_root.rglob(scene.policy['session']['full'])) == full_paths_before
            assert {str(path): path.read_bytes() for path in full_path.parent.iterdir()
                    if path.is_file()} == run_files
            assert calls.inputs == [first_source], 'F08_SERVICE_HOOK_REPARSED_OR_CAPTURED'

            following_event, following_source = ordinary_response('ordinary-after-service')
            following = chain.invoke(following_event, event_name='PostToolUse')
            assert following['hookSpecificOutput']['additionalContext'] == 'Poise: verified'
            following_full = scene.service('full')
            assert following_full['run_id'] != run_id, 'F08_ORDINARY_POISE_WAS_EXEMPTED'
            assert json.loads(Path(following_full['full_path']).read_bytes()) == following_source
            assert calls.inputs == [first_source, following_source]
            assert last.read_bytes() != last_before
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
    start = next(row for row in events if row['hook_event_name'] == 'SessionStart')
    before = next(row for row in events if row['hook_event_name'] == 'PreToolUse')
    post = next(row for row in events if row['hook_event_name'] == 'PostToolUse')
    end = next(row for row in events if row['hook_event_name'] == 'SessionEnd')
    # Establish the isolated native caller in its own real process. The existing
    # driver owns install/admission/prepare_template before actual lifecycle use.
    environment = dict(chain.general.env)
    environment.pop('POISE_CALLER_BINDING')
    environment['CODEX_THREAD_ID'] = start['session_id']
    observed = chain.general.invoke('native-builtins', environment=environment,
        settings=str(chain.general.settings_path), definition=definition,
        events=[start, before, post, end], builtin_inputs=inputs, post_wire='native-exec-json')
    pre_result = next(row for row in observed['responses']
                      if row['event']['hook_event_name'] == 'PreToolUse')
    assert pre_result['response'].get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    actual, = observed['commands']
    assert actual['actual_exit_code'] == 0 and actual['stdout'] == 'A01\nA02\n' and actual['stderr'] == ''
    delivered = next(row for row in observed['responses']
                     if row['event']['hook_event_name'] == 'PostToolUse')
    assert delivered['event']['tool_input']['command'] == before['tool_input']['command']
    assert delivered['event']['tool_response'] == {'output': actual['stdout'], 'exit_code': 0}
    assert delivered['response'].get('continue') is not False
    coverage = observed['coverage_before']
    document, = [row for row in coverage['details']['documents']
                 if row['path'] == 'docs/sample.txt']
    assert document['ranges'] == [[1, 2]]
    assert coverage['details']['counts']['pending'] == 0
    assert observed['sentinel']['task_imports'] == []
