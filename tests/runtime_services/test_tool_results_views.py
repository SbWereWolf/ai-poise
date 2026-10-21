"""Model-only limits, source-derived selection, and exact navigation framing."""
import json
from pathlib import Path

import pytest

from tests.runtime_services.tool_results_support import FIXTURES, Scenario, literal


@pytest.mark.parametrize('case', ['small-success', 'small-failure', 'large-success',
                                  'large-failure', 'no-pattern'])
def test_success_and_failure_selection(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    large = case.startswith('large') or case == 'no-pattern'
    code = 0 if case.endswith('success') else 7
    raw = (FIXTURES / ('large.stdout.log' if large else 'short.stdout.log')).read_bytes()
    if case == 'no-pattern':
        raw = raw.replace(b'ERROR:', b'NOTICE:')
    s.out.write_bytes(raw); s.err.write_bytes(b'')
    request = s.request(exit_code=code)
    value, _ = s.capture(request)
    text = value['preview']
    assert value['exit_code'] == code
    if not large:
        assert value['selection'] == 'passthrough'
        assert all(line in text for line in raw.decode().splitlines())
    elif code == 0:
        assert value['selection'] == 'success-tail'
        assert 'LAST_STDOUT' in text and 'FIRST_STDOUT' not in text
        assert 'ERROR: early failure' not in text
    else:
        assert value['selection'] == 'failure-context'
        assert 'LAST_STDOUT' in text
        if case == 'no-pattern':
            assert 'no configured failure pattern matched' in text
        else:
            assert 'ERROR: early failure' in text and 'context after error' in text
    assert s.out.read_bytes() == raw


@pytest.mark.parametrize('case', ['bytes-255', 'bytes-256', 'bytes-257',
                                  'lines-7', 'lines-8', 'lines-9', 'long-unicode-line'])
def test_byte_line_and_utf8_boundaries(project, tmp_path, case):
    s = Scenario(project, tmp_path)
    if case.startswith('bytes-'):
        raw = literal('budget-inputs.json')[case].encode()
    elif case.startswith('lines-'):
        raw = b'a\n' * int(case.split('-')[1])
    else:
        raw = (FIXTURES / 'unicode.log').read_bytes()
    s.out.write_bytes(raw)
    s.err.write_bytes(b'')
    if case == 'long-unicode-line':
        s.policy['display']['success']['max_excerpt_bytes'] = 64; s.save_policy()
    value, saved = s.capture(s.request(exit_code=0))
    assert value['selection'] == ('success-tail' if case in (
        'bytes-257', 'lines-9', 'long-unicode-line') else 'passthrough')
    text = s.calls[-1]['stdout']; s.bounded(text)
    assert text.encode().decode('utf-8') == text
    assert '\ufffd' not in value['preview']
    if case == 'long-unicode-line':
        assert len(value['preview'].encode()) <= 64
    assert Path(saved['streams']['stdout']['path']).read_bytes() == raw
    assert str(s.response_path) == value['response_path']


@pytest.mark.parametrize('kind', ['nul', 'invalid-utf8'])
def test_binary_view_preserves_raw(project, tmp_path, kind):
    s = Scenario(project, tmp_path)
    raw = (FIXTURES / 'binary.bin').read_bytes() if kind == 'nul' else b'\xffFIRST\xfeLAST'
    s.out.write_bytes(raw)
    value, saved = s.capture()
    assert value['selection'] == 'binary'
    assert saved['streams']['stdout']['binary'] is True
    assert Path(saved['streams']['stdout']['path']).read_bytes() == raw
    assert '\ufffd' not in value.get('preview', '')
    assert '\x00' not in value.get('preview', '')


@pytest.mark.parametrize('mode', ['brief', 'expanded', 'full'])
def test_three_views_preserve_source(project, tmp_path, mode):
    s = Scenario(project, tmp_path)
    s.out.write_bytes((FIXTURES / 'large.stdout.log').read_bytes())
    _, saved = s.capture()
    before = s.snapshot()
    value = s.success(s.request('view.template.json', view=mode))
    assert value['view'] == mode
    assert value['response_path'] == str(s.response_path)
    assert value['capture'] == {'state': 'complete', 'reason': None}
    if mode == 'full':
        assert set(value) == {'status', 'response_path', 'view', 'capture', 'exit_code'}
    else:
        assert 'preview' in value
        assert saved['response']['extra_source']['first'] == 'MACHINE_ONLY_FIRST'
        assert 'MACHINE_ONLY_FIRST' not in value['preview']
        if mode == 'expanded':
            assert value['details']
    assert s.snapshot() == before


@pytest.mark.parametrize('unit', ['chars', 'bytes'])
@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_navigation_includes_exact_serialization_and_lf(project, tmp_path, unit, delta):
    parent = tmp_path / 'Ж🙂'; parent.mkdir()
    s = Scenario(project, parent)
    s.capture()
    expected = literal('expected-full-view.template.json')
    expected['response_path'] = str(s.response_path)
    canonical = json.dumps(expected, ensure_ascii=False, separators=(',', ':')) + '\n'
    limit = len(canonical if unit == 'chars' else canonical.encode()) + delta
    s.policy['display']['output_' + unit] = limit; s.save_policy()
    before = s.snapshot()
    request = s.request('view.template.json', view='full')
    if delta < 0:
        s.refusal(request, 'budget_too_small', 'increase:budget')
    else:
        value = s.success(request)
        assert value == expected
        assert len(s.calls[-1]['stdout'].encode()) == len(canonical.encode())
    assert s.snapshot() == before
