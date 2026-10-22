"""Read/search exact saved sources without running the original producer."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from tests.runtime_services.tool_results_support import FIXTURES, Scenario, literal, sha, wire


@pytest.mark.parametrize('unit', ['chars', 'bytes'])
@pytest.mark.parametrize('case', ['partial', 'newline', 'empty'])
def test_final_display_range_matches_delivered_text(project, tmp_path, unit, case):
    parent = tmp_path / 'Ж🙂'; parent.mkdir()
    s = Scenario(project, parent)
    data = literal('final-range-cases.json')
    source = data['sources'][data['cases'][case]['source_id']]
    raw = source['source'].encode()
    producer = subprocess.run(
        [sys.executable, '-B', '-c',
         "import sys;open(sys.argv[1],'a').write('1');sys.stdout.write(sys.stdin.read())",
         str(s.producer_count)], input=raw, capture_output=True, env=s.env)
    assert producer.returncode == 0 and producer.stderr == b'' and producer.stdout == raw
    s.out.write_bytes(producer.stdout); s.err.write_bytes(b'')
    _, saved = s.capture(audience='machine')
    expected = {**source['full'], **data['cases'][case]['expected'], 'response_path': str(s.response_path)}
    full = {**source['full'], 'response_path': str(s.response_path)}
    envelope = wire(expected)
    s.policy['display']['output_chars'] = 20000
    s.policy['display']['output_bytes'] = 40000
    s.policy['display']['output_' + unit] = len(envelope.decode() if unit == 'chars' else envelope)
    s.save_policy()
    request = s.request('read.template.json', from_line=2, to_line=4)
    before = s.snapshot()
    try:
        # Positive source/machine control is reached before the intended agent failure.
        assert wire(s.success(request, 'machine')) == wire(full)
        value = s.success(request)
        assert wire(value) == wire(expected)
    finally:
        assert s.snapshot() == before
        assert s.producer_count.read_text() == '1'
        assert s.out.read_bytes() == raw
        assert Path(saved['streams']['stdout']['path']).read_bytes() == raw


def arrange(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.out.write_bytes((FIXTURES / 'reader.stdout.log').read_bytes())
    s.capture()
    return s


def test_summary_reports_exact_source(project, tmp_path):
    s = arrange(project, tmp_path)
    before = s.snapshot()
    value = s.success(s.request('summary.template.json'))
    raw = (FIXTURES / 'reader.stdout.log').read_bytes()
    assert value['channel'] == 'stdout'
    assert value['bytes'] == 45 and value['lines'] == 8
    assert len(raw) == 45
    assert value['sha256'] == sha(raw) and value['binary'] is False
    assert value['response_path'] == str(s.response_path)
    assert s.snapshot() == before


@pytest.mark.parametrize('case', ['normal', 'capped', 'eof', 'empty', 'utf8-misaligned'])
def test_lines_and_byte_ranges(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    raw = (FIXTURES / 'reader.stdout.log').read_bytes()
    if case == 'empty': raw = b''
    if case == 'utf8-misaligned': raw = 'Ж🙂\n'.encode()
    s.out.write_bytes(raw); s.capture()
    before = s.snapshot()
    req = s.request('read.template.json')
    if case == 'eof':
        req.update(from_line=90, to_line=91)
        s.refusal(req, 'invalid_range', 'choose:range')
    elif case == 'utf8-misaligned':
        s.refusal(s.request('bytes.template.json'), 'invalid_range', 'choose:range')
    else:
        if case in ('capped', 'empty'): req.update(from_line=1, to_line=8)
        value = s.success(req)
        assert value['response_path'] == str(s.response_path) and value['channel'] == 'stdout'
        if case == 'normal':
            assert value['text'] == literal('reader-expected.json')['lines_2_4']
            assert value['returned_lines'] == [2, 4] and value['truncated'] is False
            assert value['returned_bytes'] == [2, 17]
        elif case == 'capped':
            assert value['text'] == 'A\nB\nneedle-one\nC\n'
            assert value['returned_lines'] == [1, 4] and value['truncated'] is True
        else:
            assert value['text'] == '' and value['returned_lines'] == [0, 0]
            assert value['total_lines'] == 0 and value['truncated'] is False
    assert s.snapshot() == before


@pytest.mark.parametrize('case', ['matches', 'no-match', 'capped', 'overlap', 'invalid-regex'])
def test_search_context_and_limits(project, tmp_path, case):
    s = arrange(project, tmp_path)
    before = s.snapshot()
    req = s.request('search.template.json')
    if case == 'invalid-regex':
        req['pattern'] = '['
        s.refusal(req, 'invalid_pattern', 'correct:pattern')
    else:
        if case == 'no-match': req['pattern'] = 'ABSENT_NEEDLE'
        if case == 'capped': req.update(max_matches=1, context=0)
        value = s.success(req)
        assert value['response_path'] == str(s.response_path) and value['channel'] == 'stdout'
        expected = literal('reader-expected.json')
        if case == 'no-match':
            assert value['matches'] == [] and value['lines'] == [] and value['truncated'] is False
        elif case == 'capped':
            assert value['matches'] == expected['matches'][:1]
            assert value['lines'] == expected['matches'][:1] and value['truncated'] is True
        else:
            assert value['matches'] == expected['matches'] and value['truncated'] is True
            assert value['lines'] == expected['context']
            assert len({r['line'] for r in value['lines']}) == 5
    assert s.snapshot() == before


def test_read_replay_never_invokes_producer(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.bash_producer()
    s.capture()
    before = s.snapshot()
    for template in ('view.template.json', 'search.template.json', 'read.template.json'):
        request = s.request(template)
        first = s.success(request); second = s.success(request)
        assert first == second
    assert s.snapshot() == before
    assert s.producer_count.read_text() == '1'


@pytest.mark.parametrize('operation', ['summary', 'lines', 'search'])
def test_reader_binary_reports_metadata(project, tmp_path, operation):
    s = Scenario(project, tmp_path)
    raw = (FIXTURES / 'binary.bin').read_bytes()
    s.out.write_bytes(raw); s.capture()
    before = s.snapshot()
    template = {'summary': 'summary.template.json', 'lines': 'read.template.json',
                'search': 'search.template.json'}[operation]
    value = s.success(s.request(template))
    assert value['binary'] is True and value['response_path'] == str(s.response_path)
    assert value.get('text', '') == ''
    assert value.get('matches', []) == []
    assert s.snapshot() == before


@pytest.mark.parametrize('operation', ['lines', 'search'])
def test_inspection_excerpt_budget_and_source_identity(project, tmp_path, operation):
    s = Scenario(project, tmp_path)
    raw = (FIXTURES / 'unicode.log').read_bytes()
    s.out.write_bytes(raw)
    s.policy['inspection']['max_excerpt_bytes'] = 40; s.save_policy()
    s.capture()
    before = s.snapshot()
    if operation == 'lines':
        req = s.request('read.template.json', from_line=1, to_line=4)
        value = s.success(req)
        assert len(value['text'].encode()) <= 40 and '\ufffd' not in value['text']
        assert value['text'].startswith('FIRST\r\nЖ🙂\r\n')
        assert raw.decode().startswith(value['text'])
    else:
        req = s.request('search.template.json', pattern='Ж')
        value = s.success(req)
        assert sum(len(row['text'].encode()) for row in value['lines']) <= 40
        assert all('\ufffd' not in row['text'] for row in value['lines'])
        assert value['matches'][0] == {'line': 2, 'text': 'Ж🙂'}
        source_lines = raw.decode().splitlines()
        assert value['lines'] and all(row['text'] and
            source_lines[row['line'] - 1].startswith(row['text']) for row in value['lines'])
    assert value['truncated'] is True
    assert value['response_path'] == str(s.response_path)
    assert s.snapshot() == before


@pytest.mark.parametrize('channel', ['response', 'stderr'])
@pytest.mark.parametrize('operation', ['summary', 'lines', 'search'])
def test_reader_selects_actual_structured_or_stderr_source(project, tmp_path, channel, operation):
    s = Scenario(project, tmp_path)
    if channel == 'response':
        response = literal('mcp-response.json')
        s.capture(s.request(tool={'kind': 'mcp', 'name': 'fixture'}, response=response,
                            streams={'stdout': None, 'stderr': None}, exit_code=None), 'machine')
        raw = (FIXTURES / 'mcp-reader-source.json').read_bytes()
        expected = literal('mcp-reader-expected.json')
    else:
        raw = (FIXTURES / 'reader.stderr.log').read_bytes()
        s.err.write_bytes(raw); s.capture()
        expected = literal('stderr-reader-expected.json')
    before = s.snapshot()
    template = {'summary': 'summary.template.json', 'lines': 'read.template.json',
                'search': 'search.template.json'}[operation]
    req = s.request(template, channel=channel)
    if operation == 'lines': req.update(expected['request_lines'])
    if operation == 'search': req.update(expected['request_search'])
    value = s.success(req)
    assert value['channel'] == channel and value['response_path'] == str(s.response_path)
    if operation == 'summary':
        assert value['bytes'] == len(raw) and value['lines'] == expected['total_lines']
        assert value['sha256'] == sha(raw) and value['binary'] is False
    elif operation == 'lines':
        assert value['text'] == expected['text'] and value['text']
        assert value['returned_lines'] == expected['returned_lines']
        assert value['returned_bytes'] == expected['returned_bytes']
        assert value['truncated'] is False
    else:
        assert value['matches'] == expected['matches'] and value['lines'] == expected['context']
        assert value['truncated'] is False
    assert s.snapshot() == before


@pytest.mark.parametrize('field,value', [('context', -1), ('context', 3),
                                      ('max_matches', 0), ('max_matches', 3)])
def test_search_rejects_configured_request_bounds(project, tmp_path, field, value):
    s = arrange(project, tmp_path)
    assert s.policy['inspection']['max_context_lines'] == 2
    assert s.policy['inspection']['max_matches'] == 2
    before = s.snapshot()
    s.refusal(s.request('search.template.json', **{field: value}), 'invalid_request', 'provide:request')
    assert s.snapshot() == before


@pytest.mark.parametrize('start,end', [(0, 2), (2, 0), (4, 2)])
def test_lines_reject_zero_and_reversed_ranges(project, tmp_path, start, end):
    s = arrange(project, tmp_path); before = s.snapshot()
    s.refusal(s.request('read.template.json', from_line=start, to_line=end),
              'invalid_range', 'choose:range')
    assert s.snapshot() == before


def test_positive_utf8_aligned_byte_selection(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.out.write_bytes('AЖ🙂Z\n'.encode()); s.capture(); before = s.snapshot()
    value = s.success(s.request('bytes.template.json', start=1, end=7))
    assert value['text'] == 'Ж🙂'
    assert value['returned_bytes'] == [1, 7] and value['truncated'] is False
    assert value['channel'] == 'stdout' and value['response_path'] == str(s.response_path)
    assert s.snapshot() == before


@pytest.mark.parametrize('context,max_matches', [(0, 1), (2, 2)])
def test_search_accepts_exact_configured_request_bounds(project, tmp_path, context, max_matches):
    s = arrange(project, tmp_path); before = s.snapshot()
    value = s.success(s.request('search.template.json', context=context, max_matches=max_matches))
    expected = literal('reader-expected.json')['matches'][:max_matches]
    assert value['matches'] == expected
    assert all(row in value['lines'] for row in expected)
    assert value['channel'] == 'stdout' and value['response_path'] == str(s.response_path)
    assert s.snapshot() == before


def test_unicode_excerpt_contains_exact_bounded_source_fragment(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.out.write_bytes((FIXTURES / 'unicode.log').read_bytes())
    s.policy['inspection']['max_excerpt_bytes'] = 40; s.save_policy(); s.capture()
    before = s.snapshot()
    value = s.success(s.request('search.template.json', pattern='^(Ж🙂){2}', context=0, max_matches=1))
    assert value['lines'] == [{'line': 3, 'text': 'Ж🙂' * 6 + 'Ж'}]
    assert value['matches'] == value['lines'] and value['truncated'] is True
    assert value['channel'] == 'stdout' and value['response_path'] == str(s.response_path)
    assert s.snapshot() == before


class MachineReaderBudgetCoupling(AssertionError):
    """Only the exact real machine-reader refusal caused by the model budget."""


@pytest.mark.parametrize('operation', ['summary', 'lines', 'search', 'bytes'])
def test_machine_addressed_reader_ignores_agent_display_budget(project, tmp_path, operation):
    s = Scenario(project, tmp_path)
    raw = (FIXTURES / 'reader.stdout.log').read_bytes()
    s.out.write_bytes(raw); s.capture(audience='machine')
    before = s.snapshot()
    s.policy['display']['output_chars'] = 1
    s.policy['display']['output_bytes'] = 1; s.save_policy()
    template = {'summary': 'summary.template.json', 'lines': 'read.template.json',
                'search': 'search.template.json', 'bytes': 'bytes.template.json'}[operation]
    request = s.request(template)
    if operation == 'bytes': request.update(start=2, end=17)
    try:
        code, output, error = s.invoke(request, 'machine')
        value = json.loads(output)
        if (code == 2 and error == '' and value.get('status') == 'rejected' and
                value.get('error') == {'code': 'budget_too_small', 'field': 'display',
                                       'action': 'increase:budget'}):
            raise MachineReaderBudgetCoupling('Machine reader incorrectly applies the agent display budget')
        assert code == 0 and error == '', (code, output, error)
        assert value['status'] == 'stored' and value['channel'] == 'stdout'
        assert value['response_path'] == str(s.response_path)
        expected = literal('reader-expected.json')
        if operation == 'summary':
            assert value['bytes'] == 45 and value['lines'] == 8
            assert value['sha256'] == sha(raw) and value['binary'] is False
        elif operation == 'search':
            assert value['matches'] == expected['matches'] and value['lines'] == expected['context']
            assert value['truncated'] is True
        else:
            assert value['text'] == expected['lines_2_4']
            assert value['returned_bytes'] == [2, 17] and value['truncated'] is False
            if operation == 'lines': assert value['returned_lines'] == [2, 4]
    finally:
        assert s.snapshot() == before
