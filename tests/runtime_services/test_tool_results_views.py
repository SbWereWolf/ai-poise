"""Model-only limits, source-derived selection, and exact navigation framing."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.runtime_services.tool_results_support import FIXTURES, Scenario, literal, sha, wire


@pytest.mark.parametrize('unit', ['chars', 'bytes'])
@pytest.mark.parametrize('case', ['failure-excerpt', 'success-excerpt',
                                  'failure-global', 'success-global'])
def test_bounded_excerpt_keeps_head_and_real_tail(project, tmp_path, unit, case):
    parent = tmp_path / 'Ж🙂'; parent.mkdir()
    s = Scenario(project, parent)
    data = literal('tail-retention-cases.json'); selected = data['cases'][case]
    raw = (FIXTURES / 'tail-overflow.stdout.log').read_bytes()
    assert len(raw) == data['source_bytes'] and sha(raw) == data['source_sha256']
    producer = subprocess.run(
        [sys.executable, '-B', '-c',
         "import sys;open(sys.argv[1],'a').write('1');sys.stdout.write(sys.stdin.read())",
         str(s.producer_count)], input=raw, capture_output=True, env=s.env)
    assert producer.returncode == 0 and producer.stderr == b'' and producer.stdout == raw
    s.out.write_bytes(producer.stdout); s.err.write_bytes(b'')
    section = 'success' if selected['exit_code'] == 0 else 'failure'
    s.policy['display'][section]['max_excerpt_bytes'] = selected['excerpt']
    s.policy['display'][section]['tail_lines'] = selected['tail_lines']
    s.policy['display']['output_chars'] = 20000
    s.policy['display']['output_bytes'] = 40000
    mode = ('enforce' if (case, unit) == ('failure-global', 'chars') else
            'audit' if (case, unit) == ('success-global', 'bytes') else None)
    if mode:
        s.policy['hook']['mode'] = mode
        s.policy['hook']['max_response_bytes'] = 1
        s.policy['hook']['max_response_lines'] = 1
    s.save_policy()
    if mode:
        request = s.request('hook.template.json', exit_code=selected['exit_code'])
        saved = s.success(request, 'machine')
        s.response_path = Path(saved['response_path'])
    else:
        _, saved = s.capture(s.request(exit_code=selected['exit_code']), 'machine')
        request = s.request('view.template.json', view='brief')
    before = s.snapshot()
    assert Path(saved['streams']['stdout']['path']).read_bytes() == raw
    if case.endswith('global'):
        base = s.fill(data[(mode + '_base') if mode else 'ordinary_base'])
        base.pop('response_path', None)
        base.update(exit_code=selected['exit_code'])
        if mode == 'enforce':
            base['protocol'] = {'continue': False, 'stopReason': data['budget_preview']}
        else:
            base.update(selection=selected['selection'], preview=data['budget_preview'])
        encoded = wire(base)
        s.policy['display']['output_' + unit] = len(encoded.decode() if unit == 'chars' else encoded)
        s.save_policy()
    try:
        # The full machine result and source are controls for the very same request.
        assert wire(s.success(request, 'machine')) == wire(saved)
        value = s.success(request)
        if mode == 'enforce':
            assert value['action'] == 'enforced' and value['protocol']['continue'] is False
            preview = value['protocol']['stopReason']
            assert str(s.response_path) not in json.dumps(value)
        else:
            assert value['selection'] == selected['selection']
            preview = value['preview']
            if mode: assert value['action'] == 'would_replace'
        assert preview.startswith(selected['head'])
        assert preview.endswith(data['end']), 'The actual final source suffix was dropped'
        assert all(tail in preview for tail in data['tail'])
        assert data['marker'] in preview and value['truncated'] is True
        assert '\ufffd' not in preview and len(preview.encode()) <= selected['excerpt']
    finally:
        assert s.snapshot() == before
        assert s.producer_count.read_text() == '1'
        assert s.out.read_bytes() == raw
        assert Path(saved['streams']['stdout']['path']).read_bytes() == raw


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
    assert 'response_path' not in value


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
    assert ('response_path' in value) is (mode == 'full')
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
