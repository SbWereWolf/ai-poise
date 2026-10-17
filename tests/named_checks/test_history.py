"""Actual successful history, content reuse, explicit freshness and publication."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tomllib

from .support import FakeClock, Scene, clock_pair, digest, git, literal, snapshot, write_json


def calibrate(s, records, *, expected_sha=None):
    return s.request('calibrate', items=[{'profile': s.selection(),
                      'evidence_run_ids': [v['run_id'] for v in records],
                      'expected_profile_sha256': digest(s.profiles_path.read_bytes()) if expected_sha is None else expected_sha}])


def success_history(s):
    return [s.record(s.run(fresh=True, clock=clock_pair(1))),
            s.record(s.run(fresh=True, clock=clock_pair(2)))]


def test_initial_and_history_calibrated_limits(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    one = s.record(s.run(fresh=True, clock=clock_pair(1)))
    two = s.record(s.run(fresh=True, clock=clock_pair(2)))
    assert one['timeout']['effective_seconds'] == 10 and one['timeout']['basis'] == 'initial'
    assert two['timeout']['effective_seconds'] == 2
    s.child('--exit', '7')
    failed = s.record(s.run(status='failed', fresh=True, clock=clock_pair(9)))
    assert failed['duration_seconds'] == 9 and not failed['verified_success']
    s.child()
    next_run = s.record(s.run(fresh=True, clock=clock_pair(1)))
    assert next_run['timeout']['effective_seconds'] == 3
    assert next_run['timeout']['basis'] == 'verified_success_history'
    assert s.profile()['execution_timeout_seconds'] == 10  # Ordinary run did not publish calibration.


def test_history_compatibility_and_verified_samples(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    success_history(s)
    s.profile('different', marker='different', workload_class='other')
    other = s.record(s.run(selection=s.selection('different', workload='other'), fresh=True,
                           clock=clock_pair(20)))
    assert other['timeout']['basis'] == 'initial'
    s.child('--stdout', 'missing summary\n', parser='build')
    s.run(status='unconfirmed', fresh=True, clock=clock_pair(30))
    s.child(parser='generic')
    normal = s.record(s.run(fresh=True, clock=clock_pair(1)))
    assert normal['timeout']['effective_seconds'] == 3
    assert normal['selection']['id'] == 'sample' and len(s.markers()) == 5


def test_clock_domains_and_wall_regression(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    clock = clock_pair(2)
    record = s.record(s.run(clock=clock, fresh=True))
    assert record['duration_seconds'] == 2 and clock.calls == 2
    assert record['started']['audit_utc'] > record['finished']['audit_utc']
    assert record['started']['comparison_domain'] == record['finished']['comparison_domain'] == 'fixture-A'
    for bad in [clock_pair(2, finish_domain='fixture-B'), clock_pair(finish_ns=500000000)]:
        item = s.run(clock=bad, fresh=True, status='unconfirmed')
        actual = s.record(item)
        assert actual['actual_exit_code'] == 0 and actual['duration_seconds'] is None
        assert actual['error']['code'] == 'clock_incomparable' and not actual['verified_success']
        assert not item['cache']['stored']


def test_one_use_diagnostic_override(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.child('--sleep', '10', execution_timeout_seconds=1)
    interrupted = s.record(s.run(fresh=True, status='timed_out'))
    override = {'seconds': 2, 'evidence_run_id': interrupted['run_id'], 'reason': 'Inspect the declared slow operation once.'}
    s.child(execution_timeout_seconds=1)
    actual = s.record(s.run(fresh=True, timeout_override=override))
    assert actual['timeout']['basis'] == 'diagnostic_override'
    assert actual['timeout']['effective_seconds'] == 2
    assert actual['timeout']['evidence_run_id'] == interrupted['run_id']
    before = len(s.markers())
    s.error(s.request(fresh=True, timeout_override=override), 'override_consumed')
    assert len(s.markers()) == before


def test_override_rejects_chain_and_foreign_evidence(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    passed = s.record(s.run(fresh=True))
    s.child('--sleep', '10', execution_timeout_seconds=1)
    timed = s.record(s.run(fresh=True, status='timed_out'))
    s.child(execution_timeout_seconds=1)
    base = {'seconds': 2, 'evidence_run_id': timed['run_id'], 'reason': 'One diagnostic attempt.'}
    for change in [{'seconds': 1}, {'seconds': 31}, {'reason': ''},
                   {'evidence_run_id': passed['run_id']}, {'evidence_run_id': 'foreign-or-absent'}]:
        s.save()
        s.error(s.request(fresh=True, timeout_override={**base, **change}), 'override_invalid')
    diagnostic = s.record(s.run(fresh=True, timeout_override=base))
    s.error(s.request(fresh=True, timeout_override={**base, 'evidence_run_id': diagnostic['run_id']}), 'override_invalid')
    assert len(s.markers()) == 3


def test_cache_hit_explicit_fresh_and_release_conditions(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    original = s.run()
    unused_clock = FakeClock([])
    cached = s.run(clock=unused_clock)
    assert cached['kind'] == 'reuse' and cached['source'] == original and unused_clock.calls == 0
    assert cached['cache']['hit'] and cached['cache']['source_result_id'] == original['result_id']
    assert len(s.markers()) == 1
    fresh = s.run(fresh=True)
    assert fresh['kind'] == 'execution' and fresh['result_id'] != original['result_id']
    assert len(s.markers()) == 2
    s.profile('ordinary-final', marker='ordinary-final')
    first = s.run(selection=s.selection('ordinary-final'))
    assert s.run(selection=s.selection('ordinary-final'))['source'] == first
    s.error(s.request(final=True), 'invalid_request')
    s.profile('release-check', marker='release-check', cache={'enabled': False, 'input_paths': ['src/double.py'],
              'environment': [], 'input_selection': 'filesystem-glob'})
    s.run(selection=s.selection('release-check'))
    s.run(selection=s.selection('release-check'))
    assert [v['marker'] for v in s.markers()] == ['sample', 'sample', 'ordinary-final', 'release-check', 'release-check']


def test_actual_content_keys_and_separate_admission(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    original = s.run()
    original_key = original['cache']['key']
    s.child('--stdout', 'changed command is not a new input\n')
    monkeypatch.setenv('F07_KEY_ENV', 'changed')
    s.profile()['cache']['environment'] = ['F07_KEY_ENV']
    s.profile()['runtime_class'] = 'python-other'
    s.policy['runtime_adapters'].append({'id': 'python-other', 'platforms': ['linux'],
                                       'invocation': 'exec', 'command_processor': None})
    altered = s.run(selection=s.selection(runtime='python-other'))
    assert altered['source'] == original and altered['cache']['key'] == original_key
    (s.checkout / 'unrelated.txt').write_text('new HEAD, same selected inputs\n')
    git(s.checkout, 'add', 'unrelated.txt')
    git(s.checkout, 'commit', '-m', 'Change only unrelated membership')
    assert s.run(selection=s.selection(runtime='python-other'))['source'] == original
    s.profile()['prerequisites']['executables'] = ['missing-f07-command']
    s.save()
    s.error(s.request(selection=s.selection(runtime='python-other')), 'prerequisites_unavailable')
    s.profile()['prerequisites']['executables'] = []
    target = s.checkout / 'src/double.py'
    before_mode = target.stat().st_mode & 0o777
    target.chmod(0o600 if before_mode != 0o600 else 0o644)
    try:
        changed = s.run(selection=s.selection(runtime='python-other'))
        assert changed['kind'] == 'execution' and changed['cache']['key'] != original_key
    finally:
        target.chmod(before_mode)
    assert len(s.markers()) == 2


def test_cache_corruption_and_missing_retained_outputs(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    first = s.run()
    cache = s.state / 'cache'
    payloads = [p for p in cache.rglob('*') if p.is_file()]
    assert payloads, 'An actually stored compatible source must have a retained cache payload'
    for path in payloads:
        path.write_bytes(b'corrupt optional cache payload\n')
    second = s.run()
    assert second['kind'] == 'execution' and second['result_id'] != first['result_id']
    assert Path(first['record_path']).exists()
    Path(s.record(second)['stdout']['path']).unlink()
    third = s.run()
    assert third['kind'] == 'execution' and len(s.markers()) == 3
    assert Path(second['record_path']).exists()


def test_failed_incomplete_or_mutated_inputs_are_not_reused(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.child('--exit', '7')
    failed = s.run(status='failed')
    assert not failed['cache']['stored']
    s.child(parser='build', require_parser_confirmation=True)
    incomplete = s.run(status='unconfirmed')
    assert not incomplete['cache']['stored']
    s.child(parser='generic')
    good = s.run()
    (s.checkout / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    changed = s.run()
    assert changed['kind'] == 'execution' and changed['result_id'] != good['result_id']
    assert len(s.markers()) == 4


def test_package_and_markdown_cache_scope(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from .test_packages import diamond
    diamond(s)
    original = s.run(selection=s.selection('base', kind='package'))
    path = s.checkout / 'tests/base.py'
    mode = path.stat().st_mode & 0o777
    path.chmod(0o600 if mode != 0o600 else 0o644)
    try:
        assert s.run(selection=s.selection('base', kind='package'))['source'] == original
    finally:
        path.chmod(mode)
    path.write_text('# changed package test bytes\n')
    assert s.run(selection=s.selection('base', kind='package'))['kind'] == 'execution'
    docs = s.checkout / 'docs'
    docs.mkdir()
    (docs / 'tracked.md').write_text('# tracked\n')
    git(s.checkout, 'add', 'docs/tracked.md')
    git(s.checkout, 'commit', '-m', 'Declare tracked Markdown input')
    s.child(cache={'enabled': True, 'input_paths': [], 'environment': [],
                  'input_selection': 'git-tracked-markdown', 'max_input_files': 10, 'max_input_bytes': 10000})
    initial = s.run()
    (docs / 'ignored-untracked.md').write_text('# outside tracked set\n')
    assert s.run()['source'] == initial
    (docs / 'tracked.md').write_text('# changed tracked bytes\n')
    assert s.run()['kind'] == 'execution'


def test_explicit_calibration_preserves_unknown_raw_values(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    sentinel = literal('contract.json')['independent_oracles']['unknown_configuration']['sentinel']
    s.profile()['sentinel'] = deepcopy(sentinel)
    s.profile('unrelated', marker='unrelated')['sentinel'] = deepcopy(sentinel)
    records = success_history(s)
    before = s.profiles_path.read_bytes()
    unrelated = deepcopy(s.profile('unrelated'))
    code, result, _ = s.invoke(calibrate(s, records))
    assert code == 0 and result['status'] == 'published'
    item = result['items'][0]
    assert item['values']['execution_timeout_seconds'] == 3
    assert item['publication']['profile_written'] and item['publication']['report_written']
    assert s.profiles_path.read_bytes() != before
    parsed = tomllib.loads(s.profiles_path.read_text())
    assert parsed['commands'][0]['sentinel'] == sentinel
    assert next(v for v in parsed['commands'] if v['command_id'] == 'unrelated') == unrelated
    assert Path(item['publication']['report_path']).is_file()
    code, repeated, _ = s.invoke(calibrate(s, records))
    assert code == 0 and repeated['status'] == 'unchanged'


def test_calibration_optimistic_conflict_has_no_write(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    records = success_history(s)
    old = digest(s.profiles_path.read_bytes())
    s.profiles_path.write_bytes(s.profiles_path.read_bytes() + b'\n# independently edited\n')
    before = (s.profiles_path.read_bytes(), snapshot(s.root / 'reports'))
    s.error(calibrate(s, records, expected_sha=old), 'calibration_conflict')
    assert (s.profiles_path.read_bytes(), snapshot(s.root / 'reports')) == before


def test_calibration_partial_report_continuation(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    records = success_history(s)
    blocked = s.root / 'blocked-report'
    blocked.write_bytes(b'preserve blocking file\n')
    s.policy['publication']['report_directory'] = str(blocked)
    write_json(s.policy_path, s.policy)
    code, result, _ = s.invoke(calibrate(s, records))
    assert code == 1 and result['status'] == 'unconfirmed'
    publication = result['items'][0]['publication']
    assert publication['profile_written'] and not publication['report_written']
    assert blocked.read_bytes() == b'preserve blocking file\n'
    corrected_profile = s.profiles_path.read_bytes()
    s.policy['publication']['report_directory'] = str(s.root / 'repaired-report')
    write_json(s.policy_path, s.policy)
    code, result, _ = s.invoke(calibrate(s, records))
    assert code == 0 and result['items'][0]['publication']['report_written']
    assert not result['items'][0]['publication']['profile_written']
    assert s.profiles_path.read_bytes() == corrected_profile


def test_ordinary_runs_do_not_publish_configuration(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    before = s.profiles_path.read_bytes()
    for operation in ['list', 'describe', 'preview']:
        assert s.invoke(s.request(operation))[0] == 0
    s.run()
    s.run()
    assert s.profiles_path.read_bytes() == before
    assert not (s.root / 'reports').exists() and len(s.markers()) == 1
