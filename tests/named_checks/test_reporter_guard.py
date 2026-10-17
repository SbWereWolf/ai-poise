"""GREEN guard: expected missing entry cannot conceal an unexpected failure."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from .support import NamedChecksUnavailable, Scene, beta_project, entry_result, literal, schema
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
