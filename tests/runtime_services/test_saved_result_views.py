"""Three public read-only views of a complete saved response.

The proposed result-view CLI reuses the result_views owner; it does not open a
Task DB or replay the saved operation. Interface approval precedes implementation.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from poise.modules.result_views.domain import bounded_envelope


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'history_15_boundary.json'


def invoke(path, mode, budget=2200):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'poise', 'result-view', '--response', str(path),
         '--view', mode, '--output-chars', str(budget)],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
        capture_output=True, text=True, check=False,
    )


def saved(tmp_path, payload):
    path = tmp_path / 'response.json'
    path.write_text(json.dumps(payload, ensure_ascii=False) + '\n')
    return path


def observation(path, mode, budget=2200):
    before = path.read_bytes()
    files = sorted(str(p) for p in path.parent.rglob('*'))
    process = invoke(path, mode, budget)
    assert process.returncode == 0, process.stderr
    assert process.stderr == ''
    assert process.stdout.endswith('\n') and len(process.stdout) <= budget
    value = json.loads(process.stdout)
    assert value['view'] == mode
    assert value['response_path'] == str(path)
    assert path.read_bytes() == before
    assert sorted(str(p) for p in path.parent.rglob('*')) == files
    return value


def test_fifteen_receipt_boundary_is_exact_and_bounded():
    fixture = json.loads(FIXTURE.read_text())
    result, path = fixture['result'], fixture['response_path']
    historical_minimum = {**result, 'response_path': path}
    assert len(json.dumps(historical_minimum, ensure_ascii=False, separators=(',', ':')) + '\n') == 2232
    assert len(result['historical_receipts'][0]['checks']) == 15
    required = {key: result[key] for key in ('notice', 'historical_receipts')}
    text = bounded_envelope(result, path, 2200, {}, {}, [], required_fields=required)
    view = json.loads(text)
    assert len(text) <= 2200 and view['status'] == 'integrated'
    assert view['response_path'] == path
    assert 'historical_receipts' not in view


def test_three_views_are_distinct_and_full_is_only_navigation(tmp_path):
    payload = json.loads(FIXTURE.read_text())['result']
    path = saved(tmp_path, payload)
    brief = observation(path, 'brief')
    expanded = observation(path, 'expanded')
    full = observation(path, 'full')
    assert brief['status'] == expanded['status'] == full['status'] == 'integrated'
    assert 'details' not in brief
    assert expanded['details']['historical_receipts']
    assert set(full) == {'status', 'response_path', 'view'}
    assert json.loads(Path(full['response_path']).read_text()) == payload
    assert brief != expanded and expanded != full and brief != full


@pytest.mark.parametrize('mode', ['brief', 'expanded', 'full'])
def test_empty_result_keeps_real_file_navigation(tmp_path, mode):
    payload = {'status': 'read_only'}
    path = saved(tmp_path, payload)
    value = observation(path, mode)
    assert value['status'] == 'read_only'
    assert json.loads(Path(value['response_path']).read_text()) == payload


def test_summary_counts_unique_receipts_not_groups_or_duplicate_occurrences(tmp_path):
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    payload['historical_receipts'].append(deepcopy(payload['historical_receipts'][0]))
    payload['presentation_errors'] = [
        {'id': 'missing-a', 'presentation_error': 'owner unavailable'},
        {'id': 'missing-b', 'presentation_error': 'state lookup refused'},
    ]
    path = saved(tmp_path, payload)
    brief = observation(path, 'brief')
    assert brief['summary']['historical_receipts'] == {
        'total': 15, 'shown': 0, 'omitted': 15, 'failed': 4}
    assert brief['summary']['presentation_errors'] == {'total': 2, 'shown': 0, 'omitted': 2}
    expanded = observation(path, 'expanded')
    receipts = [record for group in expanded['details'].get('historical_receipts', [])
                for record in group['checks']]
    identifiers = [record['id'] for record in receipts]
    assert len(identifiers) == len(set(identifiers))
    assert expanded['summary']['historical_receipts'] == {
        'total': 15, 'shown': len(receipts), 'omitted': 15 - len(receipts), 'failed': 4}
    errors = expanded['details'].get('presentation_errors', [])
    assert expanded['summary']['presentation_errors'] == {
        'total': 2, 'shown': len(errors), 'omitted': 2 - len(errors)}
    assert json.loads(path.read_text()) == payload


@pytest.mark.parametrize('delta,exit_code', [(-1, 2), (0, 0), (1, 0)])
def test_full_navigation_budget_includes_lf_at_exact_boundary(tmp_path, delta, exit_code):
    path = saved(tmp_path, {'status': 'read_only'})
    expected = {'status': 'read_only', 'response_path': str(path), 'view': 'full'}
    exact = len(json.dumps(expected, ensure_ascii=False, separators=(',', ':')) + '\n')
    process = invoke(path, 'full', exact + delta)
    assert process.returncode == exit_code
    if delta >= 0:
        assert json.loads(process.stdout) == expected
        assert len(process.stdout) == exact and process.stdout.endswith('\n')
    else:
        assert process.stdout == ''
        assert process.stderr


@pytest.mark.parametrize('mode', ['brief', 'expanded', 'full'])
def test_escaped_unicode_path_and_large_optional_fields_stay_bounded(tmp_path, mode):
    directory = tmp_path / ('Ж"\\' * 20)
    directory.mkdir()
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    payload['status'] = 'ошибка"\\' * 20
    payload['reason'] = 'подробная ошибка"\\\n' * 1000
    path = saved(directory, payload)
    value = observation(path, mode)
    assert value['status'] == payload['status']
    assert json.loads(path.read_text()) == payload


def test_expanded_degrades_truthfully_when_no_record_fits(tmp_path):
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    path = saved(tmp_path, payload)
    view = observation(path, 'expanded', 650)
    summary = view['summary']['historical_receipts']
    rows = [row for group in view.get('details', {}).get('historical_receipts', []) for row in group['checks']]
    assert summary == {'total': 15, 'shown': len(rows), 'omitted': 15 - len(rows), 'failed': 4}
    assert summary['omitted'] > 0


def test_saved_view_readback_never_repeats_operation(tmp_path):
    marker = tmp_path / 'business-effect.txt'
    marker.write_text('one completed effect\n')
    path = saved(tmp_path, {'status': 'integrated', 'counter_path': str(marker)})
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    for mode in ('brief', 'expanded', 'full', 'expanded'):
        assert observation(path, mode)['status'] == 'integrated'
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


def test_missing_saved_response_is_an_error_without_fake_pointer(tmp_path):
    process = invoke(tmp_path / 'absent.json', 'full')
    assert process.returncode == 2
    assert process.stdout == '' and process.stderr
