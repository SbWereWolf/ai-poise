"""Deferred owning consumer of actual shipped configuration and examples."""
import json

import pytest

from tests.runtime_services.tool_results_support import FIXTURES, ROOT, Scenario, literal


@pytest.mark.parametrize('case', ['short', 'large', 'failure', 'structured'])
def test_documented_tool_result_examples(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    shipped = ROOT / 'config/examples'
    s.policy = s.fill(json.loads((shipped / 'tool-results.example.json').read_bytes()))
    s.save_policy()
    if case == 'structured':
        request = s.fill(json.loads((shipped / 'tool-result-mcp.example.json').read_bytes()))
        assert request['tool']['kind'] == 'mcp'
        assert request['response']['result']['content']
        assert request['response']['result']['structuredContent']
    else:
        name = 'tool-result-failure.example.json' if case == 'failure' else 'tool-result-capture.example.json'
        request = s.fill(json.loads((shipped / name).read_bytes()))
        raw = FIXTURES / ('large.stdout.log' if case in ('large', 'failure') else 'short.stdout.log')
        s.out.write_bytes(raw.read_bytes())
        # Exit is supplied by the documented producer; the example must keep it.
        assert request['exit_code'] == (7 if case == 'failure' else 0)
    _, saved = s.capture(request, 'machine')
    assert saved['response'] == request['response']
    before = s.snapshot()
    view = s.success(s.request('view.template.json'))
    assert 'response_path' not in view
    for template in ('read.template.json', 'search.template.json'):
        selected = s.request(template)
        if case == 'structured':
            selected['channel'] = 'response'
            oracle = literal('mcp-reader-expected.json')
            assert request['response'] == literal('mcp-response.json')
            selected.update(oracle['request_search'] if selected['operation'] == 'search'
                            else oracle['request_lines'])
        value = s.success(selected)
        assert value['response_path'] == str(s.response_path)
        if case == 'structured':
            assert value['channel'] == 'response'
            if selected['operation'] == 'search':
                assert value['matches'] == oracle['matches'] and value['lines'] == oracle['context']
            else:
                assert value['text'] == oracle['text'] and value['returned_lines'] == oracle['returned_lines']
    assert s.snapshot() == before
