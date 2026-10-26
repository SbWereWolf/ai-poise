"""P: real preparation producer and its actual application consumer."""
from copy import deepcopy
import json

import pytest

from tests.runtime_services.session_tool_results_support import (
    ParserCalls, SessionScenario, actual_sources, cases, prepare_owner,
)


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'mcp', 'poise'])
def test_full_source_prepares_two_independent_forms(project, tmp_path, case):
    scene = SessionScenario(project, tmp_path)
    data = cases()[case]
    _, saved = scene.stored(scene.input(case))
    assert saved['response'] == data['response']
    prepare = prepare_owner()
    with ParserCalls(prepare) as calls:
        actual = prepare(saved, actual_sources(saved), scene.policy)
    assert calls.inputs == [data['response']], 'F08_PARSER_FULL_INPUT'
    assert actual == {'brief': data['brief'], 'expanded': data['expanded']}
    assert calls.results == [actual], 'F08_PREPARED_PAIR_RETURN'
    assert 'FULL_ONLY_F08' not in actual['brief'] + actual['expanded']
    assert 'FULL_ONLY_F08' in json.dumps(saved['response'])
    print(json.dumps({'parser_inputs': calls.inputs, 'returned_pair': calls.results},
                     ensure_ascii=False))


@pytest.mark.parametrize('case', ['bash-success', 'bash-failure', 'mcp', 'poise'])
def test_application_uses_actual_pair_once(project, tmp_path, case):
    from poise.composition import tool_result_tools
    from poise.infrastructure.project_binding import read_project_checkout
    from poise.modules.result_views.tool_results import ToolResultPolicy
    scene = SessionScenario(project, tmp_path)
    data = cases()[case]
    prepare = prepare_owner()
    bound = read_project_checkout(scene.config, scene.checkout)
    owner = tool_result_tools(bound, ToolResultPolicy.parse(scene.policy).data, scene.env)
    with ParserCalls(prepare) as calls:
        actual = owner.execute(scene.input(case), 'agent')
    assert calls.inputs == [data['response']], 'F08_CONSUMER_REACHED_REAL_PARSER'
    assert calls.results == [{'brief': data['brief'], 'expanded': data['expanded']}]
    assert actual['preview'] == data['brief'], 'F08_CONSUMER_USES_PREPARED_BRIEF'
    _, full, expanded, _ = scene.run_identity(actual)
    assert expanded.read_text() == data['expanded']
    assert json.loads(full.read_bytes()) == data['response']


@pytest.mark.parametrize('case', ['bash-success', 'mcp', 'poise'])
@pytest.mark.parametrize('view', ['brief', 'expanded'])
@pytest.mark.parametrize('unit', ['chars', 'bytes'])
@pytest.mark.parametrize('delta', [0, -1], ids=['exact', 'one-short'])
def test_exact_prepared_form_budget(project, tmp_path, case, view, unit, delta):
    from poise.modules.result_views.tool_results import ToolResultFailure
    scene = SessionScenario(project, tmp_path)
    data = cases()[case]
    _, saved = scene.stored(scene.input(case))
    prepare = prepare_owner()
    expected = data[view]
    length = len(expected) if unit == 'chars' else len(expected.encode())
    scene.policy['parsers'][data['tool']['name']][view][unit] = length + delta
    if delta:
        with pytest.raises(ToolResultFailure) as error:
            prepare(saved, actual_sources(saved), scene.policy)
        assert error.value.code == 'budget_too_small'
    else:
        actual = prepare(saved, actual_sources(saved), scene.policy)
        assert actual[view] == expected


@pytest.mark.parametrize('view', ['brief', 'expanded'])
@pytest.mark.parametrize('unit', ['chars', 'bytes'])
@pytest.mark.parametrize('delta', [0, -1], ids=['exact', 'one-short'])
def test_unicode_budget_uses_actual_units(project, tmp_path, view, unit, delta):
    from poise.modules.result_views.tool_results import ToolResultFailure
    from tests.runtime_services.tool_results_support import literal
    scene = SessionScenario(project, tmp_path)
    data = literal('session-special-cases.json')['crlf-unicode']
    request = scene.input()
    request['response'] = data['response']
    _, saved = scene.stored(request)
    prepare = prepare_owner()
    expected = data[view]
    assert len(expected.encode()) > len(expected)
    size = len(expected) if unit == 'chars' else len(expected.encode())
    scene.policy['parsers']['Bash'][view][unit] = size + delta
    if delta:
        with pytest.raises(ToolResultFailure) as error:
            prepare(saved, actual_sources(saved), scene.policy)
        assert error.value.code == 'budget_too_small'
    else:
        assert prepare(saved, actual_sources(saved), scene.policy)[view] == expected


@pytest.mark.parametrize('case', ['empty', 'crlf-unicode', 'stderr-only', 'binary-mcp'])
def test_exact_special_source_and_unicode(project, tmp_path, case):
    from tests.runtime_services.tool_results_support import literal
    scene = SessionScenario(project, tmp_path)
    data = literal('session-special-cases.json')[case]
    request = scene.input(data['base'])
    request.update(response=data['response'], exit_code=data['exit_code'])
    for channel in ('stdout', 'stderr'):
        text = data['streams'][channel]
        if text is not None:
            path = tmp_path / (channel + '.literal')
            path.write_bytes(text.encode())
            request['streams'][channel] = str(path)
    _, saved = scene.stored(request)
    prepare = prepare_owner()
    actual = prepare(saved, actual_sources(saved), scene.policy)
    assert actual == {'brief': data['brief'], 'expanded': data['expanded']}
    assert saved['response'] == data['response']
    for channel, expected in data['streams'].items():
        if expected is not None:
            from pathlib import Path
            assert Path(saved['streams'][channel]['path']).read_bytes() == expected.encode()
    assert '\ufffd' not in actual['brief'] + actual['expanded']
    if case == 'crlf-unicode':
        assert len(actual['brief'].encode()) > len(actual['brief'])
        assert '\r\n' in saved['response']['output']


@pytest.mark.parametrize('case,code', [('unknown-parser', 'unmapped_input'),
                                     ('bad-mcp', 'parser_failed')])
def test_parser_producer_refuses_meaningfully(project, tmp_path, case, code):
    from poise.modules.result_views.tool_results import ToolResultFailure
    scene = SessionScenario(project, tmp_path)
    _, saved = scene.stored(scene.input('mcp'))
    prepare = prepare_owner()
    if case == 'unknown-parser':
        scene.policy['parsers']['mcp__fixture__query']['parser'] = 'not-registered'
    else:
        saved = deepcopy(saved)
        saved['response']['structuredContent'].pop('count')
    with pytest.raises(ToolResultFailure) as error:
        prepare(saved, actual_sources(saved), scene.policy)
    assert error.value.code == code


@pytest.mark.parametrize('case,code', [('unknown-parser', 'unmapped_input'),
                                     ('bad-mcp', 'parser_failed')])
def test_capture_consumer_keeps_failure_and_source(project, tmp_path, case, code):
    scene = SessionScenario(project, tmp_path)
    scene.stored(scene.input())
    before = scene.snapshot()
    request = scene.input('mcp', key='bad-parser-second-call')
    if case == 'unknown-parser':
        scene.policy['parsers']['mcp__fixture__query']['parser'] = 'not-registered'
    else:
        request['response']['structuredContent'].pop('count')
    scene.save_policy()
    exit_code, actual = scene.direct(request)
    assert exit_code == 2 and actual.get('status') == 'rejected', 'F08_PARSER_FAILURE_NOT_REFUSED'
    assert actual['error']['code'] == code
    assert scene.snapshot() == before, 'F08_PARSER_FAILURE_PUBLISHED_A_RUN'
