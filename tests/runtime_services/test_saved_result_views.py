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
TWO_TASKS = Path(__file__).with_name('fixtures') / 'history_two_tasks.json'


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


def assert_selected_source_records(payload, expanded):
    expected = {(group['task'], row['id']): row
                for group in payload['historical_receipts'] for row in group['checks']}
    actual = []
    for group in expanded.get('details', {}).get('historical_receipts', []):
        for row in group['checks']:
            identity = (group['task'], row['id'])
            assert identity in expected
            assert row == expected[identity]
            assert {'id', 'method', 'actual_exit_code', 'passed'} <= set(row)
            actual.append(identity)
    assert len(actual) == len(set(actual))
    errors = expanded.get('details', {}).get('presentation_errors', [])
    source_errors = payload.get('presentation_errors', [])
    for error in errors:
        assert error in source_errors
        assert errors.count(error) <= source_errors.count(error)
    return actual


def test_fifteen_receipt_boundary_is_exact_readable_and_bounded(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    result, path = fixture['result'], fixture['response_path']
    historical_minimum = {**result, 'response_path': path}
    assert len(json.dumps(historical_minimum, ensure_ascii=False, separators=(',', ':')) + '\n') == 2232
    assert len(result['historical_receipts'][0]['checks']) == 15
    # Replace only the test root, adjusting test-owned padding to retain the
    # exact input boundary. Both nested components are valid ordinary names.
    suffix = Path('x' * 144) / 'response.json'
    padding = len(path) - len(str(tmp_path / suffix)) - 1
    assert 0 < padding <= 143
    actual_path = tmp_path / ('x' * padding) / suffix
    actual_path.parent.mkdir(parents=True)
    actual_path.write_text(json.dumps(result, ensure_ascii=False) + '\n')
    before = actual_path.read_bytes()
    path = str(actual_path)
    assert len(json.dumps({**result, 'response_path': path},
                          ensure_ascii=False, separators=(',', ':')) + '\n') == 2232
    required = {key: result[key] for key in ('notice', 'historical_receipts')}
    text = bounded_envelope(result, path, 2200, {}, {}, [], required_fields=required)
    view = json.loads(text)
    assert len(text) <= 2200 and view['status'] == 'integrated'
    assert view['response_path'] == path
    assert 'historical_receipts' not in view
    assert actual_path.read_bytes() == before


def test_three_views_are_distinct_and_full_is_only_navigation(tmp_path):
    payload = json.loads(TWO_TASKS.read_text())
    path = saved(tmp_path, payload)
    brief = observation(path, 'brief')
    expanded = observation(path, 'expanded')
    full = observation(path, 'full')
    assert brief['status'] == expanded['status'] == full['status'] == 'integrated'
    assert 'details' not in brief
    assert expanded['details']['historical_receipts']
    assert assert_selected_source_records(payload, expanded)
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
    payload = json.loads(TWO_TASKS.read_text())
    payload['historical_receipts'].append(deepcopy(payload['historical_receipts'][0]))
    path = saved(tmp_path, payload)
    brief = observation(path, 'brief')
    assert brief['summary']['historical_receipts'] == {
        'total': 15, 'shown': 0, 'omitted': 15, 'failed': 4}
    assert brief['summary']['presentation_errors'] == {'total': 2, 'shown': 0, 'omitted': 2}
    expanded = observation(path, 'expanded')
    assert_selected_source_records(payload, expanded)
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


@pytest.mark.parametrize('mode', ['brief', 'expanded', 'full'])
def test_receipt_identity_cannot_have_conflicting_task_owners(tmp_path, mode):
    payload = json.loads(TWO_TASKS.read_text())
    # Receipt identity is global. Keeping the original bytes under a different
    # Task is still conflicting ownership, not a second distinct receipt.
    payload['historical_receipts'][1]['checks'].append(
        deepcopy(payload['historical_receipts'][0]['checks'][0]))
    path = saved(tmp_path, payload)
    before = path.read_bytes()
    files = sorted(str(item) for item in tmp_path.rglob('*'))
    process = invoke(path, mode)
    assert process.returncode == 2
    assert process.stdout == ''
    diagnostic = process.stderr.lower()
    assert 'receipt' in diagnostic and 'owner' in diagnostic and 'conflict' in diagnostic
    assert path.read_bytes() == before
    assert sorted(str(item) for item in tmp_path.rglob('*')) == files


@pytest.mark.parametrize('mode', ['brief', 'expanded', 'full'])
@pytest.mark.parametrize('delta,exit_code', [(-1, 2), (0, 0), (1, 0)])
def test_navigation_budget_includes_mode_and_lf_at_exact_boundary(tmp_path, mode, delta, exit_code):
    path = saved(tmp_path, {'status': 'read_only'})
    expected = {'status': 'read_only', 'response_path': str(path), 'view': mode}
    exact = len(json.dumps(expected, ensure_ascii=False, separators=(',', ':')) + '\n')
    process = invoke(path, mode, exact + delta)
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


def test_expanded_degrades_truthfully_when_history_does_not_fit(tmp_path):
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    path = saved(tmp_path, payload)
    view = observation(path, 'expanded', 650)
    summary = view['summary']['historical_receipts']
    rows = [row for group in view.get('details', {}).get('historical_receipts', []) for row in group['checks']]
    assert summary == {'total': 15, 'shown': len(rows), 'omitted': 15 - len(rows), 'failed': 4}
    assert summary['omitted'] > 0
    assert_selected_source_records(payload, view)


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


@pytest.mark.parametrize('text', [
    '{', '[]', 'null', '{}', '{"status":42}', '{"status":null}',
    '{"status":""}', '{"status":[]}', '{"status":true}',
])
def test_invalid_saved_result_is_rejected_without_modification(tmp_path, text):
    path = tmp_path / 'response.json'
    path.write_text(text)
    before = path.read_bytes()
    process = invoke(path, 'brief')
    assert process.returncode == 2
    assert process.stdout == '' and process.stderr
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('budget', [0, -1, 'invalid', '1.5'])
def test_invalid_budget_is_rejected_without_effects(tmp_path, budget):
    path = saved(tmp_path, {'status': 'read_only'})
    before = path.read_bytes()
    process = invoke(path, 'brief', budget)
    assert process.returncode == 2
    assert process.stdout == '' and process.stderr
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_unsupported_view_is_rejected_without_effects(tmp_path):
    path = saved(tmp_path, {'status': 'read_only'})
    before = path.read_bytes()
    process = invoke(path, 'unknown')
    assert process.returncode == 2
    assert process.stdout == '' and process.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize('boundary', ['minimum', 'summary-minus-one', 'summary',
                                     'detail-minus-one', 'detail'])
def test_summary_and_detail_drop_boundaries(tmp_path, boundary):
    source = json.loads(TWO_TASKS.read_text())
    row = source['historical_receipts'][0]['checks'][0]
    payload = {'status': 'integrated', 'historical_receipts': [
        {'task': 'TASK-A', 'checks': [row]}]}
    path = saved(tmp_path, payload)
    minimal = {'status': 'integrated', 'response_path': str(path), 'view': 'expanded'}
    summary = {'historical_receipts': {'total': 1, 'shown': 0, 'omitted': 1, 'failed': 1}}
    complete = {**minimal, 'summary': {'historical_receipts': {
        'total': 1, 'shown': 1, 'omitted': 0, 'failed': 1}},
        'details': {'historical_receipts': [{'task': 'TASK-A', 'checks': [row]}]}}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
    limits = {'minimum': size(minimal), 'summary-minus-one': size({**minimal, 'summary': summary}) - 1,
              'summary': size({**minimal, 'summary': summary}),
              'detail-minus-one': size(complete) - 1, 'detail': size(complete)}
    value = observation(path, 'expanded', limits[boundary])
    if boundary in ('minimum', 'summary-minus-one'):
        assert value == minimal
    elif boundary in ('summary', 'detail-minus-one'):
        assert value == {**minimal, 'summary': summary}
    else:
        assert value == complete
    assert_selected_source_records(payload, value)
