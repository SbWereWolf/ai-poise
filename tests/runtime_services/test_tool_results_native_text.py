"""Actual-shaped text producer/consumer contracts; no product implementation.

Feature cases are RED until the accepted native-text behavior exists. Old
object-profile denial cases are separate unchanged GREEN controls.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from poise.modules.result_views.tool_results import (
    ToolResultFailure, ToolResultPolicy, native_snapshot, select_parser,
)
from tests.runtime_services.native_wire_support import NativeCalls, select_text, wire_case
from tests.runtime_services.session_tool_results_support import SessionScenario
from tests.runtime_services.tool_results_support import FIXTURES


@pytest.mark.parametrize('case', ['healthy', 'empty', 'json-looking', 'unicode-nul'])
def test_native_text_classifier_keeps_exact_value_and_unknown_process_facts(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    event = scene.native(wire_case(case)['raw'])['event']
    original = deepcopy(event)
    policy = ToolResultPolicy.parse(scene.policy).data
    name, profile = select_parser(event, policy)
    assert (name, profile['format']) == ('Bash', 'native-text')
    with NativeCalls() as calls:
        try:
            actual = native_snapshot(event, policy)
        finally:
            assert calls.events == [original], 'NATIVE_TEXT_CLASSIFIER_NOT_REACHED'
    assert actual['response'] == wire_case(case)['raw']
    assert type(actual['response']) is str
    assert actual['exit_code'] is None
    assert actual['capture'] == {'state': 'complete', 'reason': None}
    assert actual['pending'] is False and event == original
    print(json.dumps({'boundary': 'real-native-snapshot', 'event': original,
                      'actual': actual}, ensure_ascii=False))


@pytest.mark.parametrize('case', ['healthy', 'empty', 'json-looking', 'unicode-nul'])
def test_native_text_public_capture_and_services_keep_original_physical_text(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    expected = wire_case(case)
    request = scene.native(expected['raw'])
    original = deepcopy(request)
    code, saved = scene.direct(request, audience='machine')
    assert code == 0 and saved['status'] == 'stored', ('NATIVE_TEXT_PUBLIC_CAPTURE', code, saved)
    assert request == original and saved['response'] == expected['raw']
    assert type(saved['response']) is str and saved['exit_code'] is None
    assert saved['streams'] == {'stdout': None, 'stderr': None}
    assert json.loads(Path(saved['response_path']).read_bytes())['response'] == expected['raw']
    run_id, full, expanded, last = scene.run_identity(saved)
    assert full.read_bytes() == expected['raw'].encode(), 'NATIVE_TEXT_FULL_ESCAPED_OR_CHANGED'
    assert expanded.read_text() == expected['expanded']
    assert saved['_prepared'] == {'brief': expected['brief'], 'expanded': expected['expanded']}
    before = scene.snapshot()
    assert scene.service('expanded')['text'] == expected['expanded']
    answer = scene.service('full')
    assert answer['run_id'] == run_id and answer['full_path'] == str(full)
    assert json.loads(last.read_bytes()) == {'uuid': run_id}
    assert scene.snapshot() == before, 'NATIVE_TEXT_SERVICE_CHANGED_SOURCE_OR_LAST'


@pytest.mark.parametrize('case', ['partial', 'unknown'])
def test_native_text_loss_cannot_return_older_complete_full(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    # Arrange the predecessor through its unchanged structured profile; it must
    # not depend on a future raw-text parser accepting a dictionary as text.
    prior, _ = scene.stored(scene.input(key='complete-before-native-text-loss'))
    select_text(scene)
    expected = wire_case(case)
    code, current = scene.direct(scene.native(expected['raw'], key='text-loss-' + case),
                                 audience='machine')
    assert code == 0 and current['status'] == 'stored', ('NATIVE_TEXT_LOSS_CAPTURE', code, current)
    assert current['response'] == expected['raw'] and current['exit_code'] is None
    assert current['capture'] == {'state': expected['state'], 'reason': expected['reason']}
    assert current['run_id'] != prior['run_id']
    before = scene.snapshot()
    code, answer = scene.direct({'schema': 'poise-tool-result-request-1', 'operation': 'full'})
    assert code == 2 and answer['error']['code'] == 'source_incomplete'
    assert 'full_path' not in answer
    assert scene.service('expanded')['run_id'] == current['run_id']
    assert scene.snapshot() == before


@pytest.mark.parametrize('exit_code', [0, 7])
def test_real_process_text_capture_does_not_invent_its_unavailable_native_exit(project, tmp_path, exit_code):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    source = tmp_path / 'literal-producer.txt'
    source.write_text(wire_case('healthy')['raw'])
    argv = [sys.executable, '-B', str(FIXTURES / 'session_producer.py'),
            '--stdout', str(source), '--counter', str(scene.producer_count),
            '--exit-code', str(exit_code)]
    producer = subprocess.run(argv, capture_output=True, check=False)
    assert producer.returncode == exit_code and producer.stdout == source.read_bytes()
    assert producer.stderr == b'' and scene.producer_count.read_text() == '1'
    # Real process observation stays separate from the actually observed hook
    # shape: the callback contains only text, not an invented execution object.
    request = scene.native(producer.stdout.decode())
    code, result = scene.direct(request, audience='machine')
    assert code == 0, ('NATIVE_TEXT_ACTUAL_PROCESS_SOURCE', code, result)
    assert result['exit_code'] is None and result['streams'] == {'stdout': None, 'stderr': None}
    assert result['response'] == source.read_text()
    assert Path(result['full_path']).read_bytes() == source.read_bytes()
    assert scene.producer_count.read_text() == '1'
    print(json.dumps({'argv': argv, 'observed_process_exit': producer.returncode,
                      'actual_native_input': request['event'], 'record': result}, ensure_ascii=False))


def test_native_text_large_full_file_has_readable_original_line_ranges(project, tmp_path):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    raw = (FIXTURES / 'native_wire' / 'large.txt').read_bytes()
    code, record = scene.direct(scene.native(raw.decode()), audience='machine')
    assert code == 0, ('NATIVE_TEXT_LARGE_SOURCE', code, record)
    assert record['response'].encode() == raw and record['capture']['state'] == 'complete'
    _, full, expanded, _ = scene.run_identity(record)
    assert full.read_bytes() == raw and len(raw) > 512
    ranged = subprocess.run(['sed', '-n', '42,42p', str(full)], capture_output=True, check=False)
    assert ranged.returncode == 0 and ranged.stderr == b''
    assert ranged.stdout == (FIXTURES / 'native_wire' / 'range.txt').read_bytes()
    assert 'FULL_ONLY_NATIVE_WIRE_42' not in record['_prepared']['brief']
    assert 'FULL_ONLY_NATIVE_WIRE_42' not in expanded.read_text()


@pytest.mark.parametrize('case', ['healthy', 'empty', 'json-looking'])
def test_legacy_object_wire_remains_strict_about_text(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    event = scene.native(wire_case(case)['raw'])['event']
    assert scene.policy['parsers']['Bash']['format'] == 'native-exec-json'
    with pytest.raises(ToolResultFailure) as error:
        native_snapshot(event, scene.policy)
    assert (error.value.code, error.value.field, error.value.action) == (
        'unmapped_input', 'tool_response', 'configure:input')


@pytest.mark.parametrize('value', [None, 7, False,
                                   pytest.param({'output': 'ready', 'exit_code': 0},
                                                id='object-envelope')])
def test_native_text_wrong_value_type_is_rejected_at_response_boundary(project, tmp_path, value):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    event = scene.native(value)['event']
    with NativeCalls() as calls:
        with pytest.raises(ToolResultFailure) as refused:
            native_snapshot(event, scene.policy)
    assert calls.events == [event]
    assert (refused.value.code, refused.value.field, refused.value.action) == (
        'unmapped_input', 'tool_response', 'configure:input')


@pytest.mark.parametrize('field', ['brief_template', 'brief_without_summary'])
def test_native_text_policy_rejects_templates_that_require_unavailable_exit(project, tmp_path, field):
    scene = SessionScenario(project, tmp_path)
    select_text(scene)
    # Healthy declared policy admission is a distinct positive prerequisite.
    assert ToolResultPolicy.parse(scene.policy).data == scene.policy
    scene.policy['parsers']['Bash'][field] = 'Bash: exit {exit_code}'
    with pytest.raises(ToolResultFailure) as refused:
        ToolResultPolicy.parse(scene.policy)
    assert refused.value.code == 'invalid_policy'


@pytest.mark.parametrize('case', ['missing', 'invalid-envelope', 'outside-root'])
def test_saved_work_text_source_failures_are_precise_and_do_not_invent_exit(project, tmp_path, case):
    from poise.infrastructure.tool_results import native_result
    from tests.runtime_services.native_wire_support import select_work_text
    from tests.runtime_services.tool_results_support import literal
    scene = SessionScenario(project, tmp_path)
    source_root = tmp_path / 'admitted-saved-work'
    source_root.mkdir()
    select_work_text(scene, source_root)
    envelope = literal('native_wire/work-envelope.template.json')
    envelope['response_path'] = str(source_root / 'missing.json')
    if case == 'outside-root':
        outside = tmp_path / 'foreign-work.json'
        outside.write_text('{"status":"verified"}\n')
        envelope['response_path'] = str(outside)
    raw = 'not a work envelope\n' if case == 'invalid-envelope' else json.dumps(envelope) + '\n'
    event = scene.native(raw)['event']
    event['tool_input']['command'] = 'python -m poise work'
    original = deepcopy(event)
    with NativeCalls() as calls:
        with pytest.raises(ToolResultFailure) as refused:
            native_result(event, scene.policy)
    assert calls.events == [original] and event == original
    expected = ('source_missing', 'response_path', 'provide:source') if case == 'missing' else (
        'source_invalid', 'response_path', 'restore:source')
    actual = (refused.value.code, refused.value.field, refused.value.action)
    assert actual == expected, ('SAVED_WORK_TEXT_SOURCE_FAILURE', case, actual)
