"""GREEN guard: expected missing entry cannot conceal an unexpected failure."""
from copy import deepcopy
from types import SimpleNamespace
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from .support import NamedChecksUnavailable, Scene, beta_project, entry_result, functional_snapshot, literal, logical_ledger, schema
from tests.skills.record_instruction_red import canonical


def test_entry_failure_requires_all_observed_conditions():
    missing = SimpleNamespace(returncode=2, stdout=b'',
                              stderr=b"usage: poise\nargument command: invalid choice: 'checks'\n")
    with pytest.raises(NamedChecksUnavailable):
        entry_result(missing)
    for change in [{'returncode': 1}, {'stdout': b'unexpected'}, {'stderr': b'import failed'},
                   {'stderr': b"argument command: invalid choice: 'checks'"},
                   {'stderr': b'usage: poise\ninvalid configuration'}]:
        actual = SimpleNamespace(**{**missing.__dict__, **change})
        with pytest.raises(AssertionError) as caught:
            entry_result(actual)
        assert type(caught.value) is AssertionError
    entry_result(SimpleNamespace(returncode=0, stdout=b'checks help\n', stderr=b''))


def test_literal_event_expectations_reject_each_unexpected_outcome():
    for group in ['contracts', 'execution', 'history', 'packages']:
        expected = literal(group + '-red-events.json')
        assert len(expected['reports']) == 42 and expected['exit_code'] == 1
        assert sum(v['phase'] == 'call' and v['outcome'] == 'failed' for v in expected['reports']) == 14
        for mutation in ['collection', 'setup', 'teardown', 'skipped', 'wrong_exception', 'extra', 'missing']:
            actual = deepcopy(expected)
            row = next(v for v in actual['reports'] if v['phase'] == 'call')
            if mutation == 'collection':
                actual['collection_errors'] = ['unexpected collection']
            elif mutation in ['setup', 'teardown']:
                next(v for v in actual['reports'] if v['phase'] == mutation)['outcome'] = 'failed'
            elif mutation == 'skipped':
                row['outcome'] = 'skipped'
            elif mutation == 'wrong_exception':
                row['exception'] = 'ImportError'
            elif mutation == 'extra':
                actual['reports'].append({**row, 'nodeid': 'extra'})
            else:
                actual['reports'].remove(row)
            assert canonical(actual['exit_code'], actual['collection_errors'], actual['reports']) != canonical(
                expected['exit_code'], expected['collection_errors'], expected['reports'])


def test_contract_assertions_reject_type_field_and_union_mistakes():
    spec = literal('schema-guard.json')
    schema({'enabled': True}, spec)
    for broken in [{}, {'enabled': 1}, {'enabled': True, 'extra': None}]:
        with pytest.raises(AssertionError):
            schema(broken, spec)
    with pytest.raises(AssertionError):
        schema(True, {'type': 'integer'})
    with pytest.raises(AssertionError):
        schema('ambiguous', {'oneOf': [{'type': 'string'}, {'type': 'string'}]})


def test_fixture_arrangement_roundtrips_before_future_behavior(project, tmp_path, monkeypatch):
    # This GREEN guard checks test inputs only. It never calls or simulates the
    # missing named-check producer and is not evidence of any product behavior.
    calls = []
    from . import support
    monkeypatch.setattr(support, 'entry', lambda: calls.append('entry'))
    arranged = Scene(project, tmp_path, monkeypatch)
    contracts = literal('contract.json')['schemas']
    schema(arranged.policy, contracts['configuration']['policy'])
    schema(arranged.profiles, contracts['configuration']['profiles_toml'])
    schema(arranged.packages, contracts['configuration']['packages_toml'])
    schema(arranged.suites, contracts['configuration']['suites_json'])
    schema(literal('package-impact-catalogue.json'), contracts['configuration']['packages_toml'])
    for declaration in literal('package-impact-expected-declarations.json'):
        schema(declaration, contracts['package_declaration'])
    schema(arranged.request(), contracts['request'])
    schema(arranged.request('package_impact', items=[{'changed_paths': []}]), contracts['request'])
    other, other_root = beta_project(project, tmp_path)
    beta = Scene(other, other_root, monkeypatch)
    assert arranged.config != beta.config and arranged.checkout != beta.checkout
    assert arranged.state != beta.state and calls == ['entry', 'entry']


def test_logical_ledger_observer_distinguishes_data_from_bookkeeping(tmp_path):
    # Synthetic probe payloads test the observer, never successful check records
    # or calibration evidence. No named-facility producer is simulated here.
    path = tmp_path / 'observer.sqlite'
    assert logical_ledger(path) == {}
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        connection.execute('CREATE TABLE probe_payloads(payload TEXT)')
        connection.execute("INSERT INTO metadata(key,value) VALUES('diagnostic','one')")
    assert logical_ledger(path) == {}
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE metadata SET value='two' WHERE key='diagnostic'")
        connection.execute("INSERT INTO probe_payloads(payload) VALUES('unexpected functional payload')")
    observed = logical_ledger(path)
    assert observed == {'probe_payloads': (['payload'], [('unexpected functional payload',)])}
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM probe_payloads')
    # Physical SQLite bytes/header changed; logical application data is empty.
    assert logical_ledger(path) == {}
    state = tmp_path / 'owned-state'
    state.mkdir()
    storage = literal('input-policy.json')['storage']
    baseline = functional_snapshot(state, storage)
    cache = state / storage['cache_directory'] / 'cache.sqlite'
    cache.parent.mkdir()
    with sqlite3.connect(cache) as connection:
        connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        connection.execute('CREATE TABLE probe_payloads(payload TEXT)')
        connection.execute("INSERT INTO metadata(key,value) VALUES('diagnostic','one')")
    responses = state / storage['response_directory']
    responses.mkdir()
    (responses / 'probe.json').write_text('{"diagnostic":"allowed"}\n')
    (state / storage['lock_file']).write_text('lock bookkeeping\n')
    assert functional_snapshot(state, storage) == baseline
    with sqlite3.connect(cache) as connection:
        connection.execute("UPDATE metadata SET value='two' WHERE key='diagnostic'")
    assert functional_snapshot(state, storage) == baseline
    with sqlite3.connect(cache) as connection:
        connection.execute("INSERT INTO probe_payloads(payload) VALUES('unexpected cache payload')")
    assert functional_snapshot(state, storage) != baseline
    with sqlite3.connect(cache) as connection:
        connection.execute('DELETE FROM probe_payloads')
    assert functional_snapshot(state, storage) == baseline
    produced = state / storage['runs_directory'] / 'probe.json'
    produced.parent.mkdir()
    produced.write_text('{"probe":"unexpected produced data"}\n')
    assert functional_snapshot(state, storage) != baseline


def test_paced_progress_clock_delegates_actual_capture_and_deadlines(tmp_path, monkeypatch):
    from poise.execution import RegisteredCheckRunner
    from .progress_clock import CapturedProgressClock
    specification = literal('stall-history-cases.json')
    events = tmp_path / 'events'
    events.mkdir()
    exit_file = events / 'exit.txt'
    argv = [sys.executable, '-B', str(Path(__file__).with_name('fixture_child.py')),
            '--marker', 'fixture-clock', '--paced-progress', '--exit-file', str(exit_file)]
    for name in ['valid-small', 'failed-large']:
        case = next(v for v in specification['cases'] if v['name'] == name)
        exit_file.write_text(str(case['child_exit']) + '\n')
        clock = CapturedProgressClock(events, specification, case)
        assert clock.observe().monotonic_ns == specification['started_ns']
        with monkeypatch.context() as patch:
            clock.install(patch)
            result = RegisteredCheckRunner().run(
                'fixture-clock-' + name, argv,
                tmp_path, {**os.environ, 'F07_FIXTURE_EVENTS': str(events)}, 10,
                tmp_path / 'stdout', tmp_path / 'stderr', progress_gap_seconds=5)
        assert result is clock.results[0] and result == clock.result_snapshots[0]
        assert result['actual_exit_code'] == case['child_exit'] and result['capture_complete']
        assert not result['timed_out'] and not result['cancelled']
        assert clock.forwarded == [{'progress_gap_seconds': 5, 'timeout': 10}]
        assert [v[1].monotonic_ns for v in clock.captured] == case['progress_ns']
        assert clock.observe().monotonic_ns == case['finish_ns']
        assert (tmp_path / 'stdout').read_bytes() == specification['raw_stdout'].encode()
        assert (tmp_path / 'stderr').read_bytes() == b''
