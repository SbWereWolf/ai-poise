"""Independent regressions for stale recovery and unsupported owner records.

The SDK supplies real isolated episodes. Only entry into the ordinary gate is
scheduled; native observations and the filesystem/SQLite effects remain real.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from tests.content_reader.helpers import (
    Scenario, files, fixture, strict_json, typed_equal,
)
from tests.content_reader.contract_checks import address_shape


def owner_record(scenario):
    return (Path(scenario.data['session']['coordination_root']) / scenario.caller_key
            / scenario.data['session']['ownership_filename'])


def expected_result(scenario, kind, path, *, episode=None, resource=None):
    result = scenario.fill(deepcopy(fixture('owner-regressions.json')[kind]))
    result['response_path'] = path
    if result['context'] is not None:
        result['context']['checkout_key'] = scenario.checkout_keys[str(scenario.checkout)]
        result['context']['native_episode'] = dict(scenario.episode)
    if kind == 'stale_recovery':
        result['details']['episode'] = dict(episode)
        result['details']['path'] = str(resource)
    return result


def assert_response(scenario, actual, kind, code, *, episode=None, resource=None):
    returned, stdout, stderr = actual
    typed_equal(returned, code)
    assert stdout, 'The public boundary must return a complete structured response'
    assert stderr == '', 'A managed refusal must not escape as a traceback'
    value = strict_json(stdout)
    scenario.envelope(value, operation=value['operation'], exit_code=code)
    path = value['response_path']
    address_shape(path, Path(scenario.data['storage']['control_response_root']))
    expected = expected_result(scenario, kind, path, episode=episode, resource=resource)
    typed_equal(value, expected)
    typed_equal(strict_json(Path(path).read_bytes()), expected)
    return value


@pytest.mark.parametrize('api', ['cli', 'python'])
@pytest.mark.parametrize('case', ['valid', 'unsupported', 'malformed'])
def test_owner_records_return_structured_diagnostics(project, tmp_path, monkeypatch, api, case):
    s = Scenario(project, tmp_path)
    s.ready()
    spec = fixture('owner-regressions.json')
    s.row(s.read(), text=spec['selected_content'], selection='lines:2-3', classification='first_read')
    owner = owner_record(s)
    original = owner.read_bytes()
    before_tree, before_rows = files(s.resource), s.metadata()
    if case == 'unsupported':
        documents = [entry['raw'].encode('utf-8') for entry in spec['unsupported_documents']]
        assert [entry['kind'] for entry in spec['unsupported_documents']] == [
            'array', 'null', 'boolean', 'number', 'string',
        ]
    elif case == 'malformed':
        documents = [spec['malformed_document'].encode('utf-8')]
    else:
        documents = [original]
    try:
        for raw in documents:
            owner.write_bytes(raw)
            request = {'operation': 'context'}
            actual = s.raw(request) if api == 'cli' else s.transport(monkeypatch, request)
            assert_response(s, actual, 'valid_context' if case == 'valid' else 'owner_failure',
                            0 if case == 'valid' else 1)
            assert owner.read_bytes() == raw
            assert files(s.resource) == before_tree
            assert s.metadata() == before_rows
    finally:
        owner.write_bytes(original)
    # Restoration is an actual positive control, not a substituted failure input.
    assert_response(s, s.raw({'operation': 'context'}), 'valid_context', 0)


def test_stale_recover_preserves_new_episode(project, tmp_path, monkeypatch):
    s = Scenario(project, tmp_path)
    spec = fixture('owner-regressions.json')
    s.data['coordination'].update(spec['coordination'])
    s.save_policy()
    s.ready()
    s.read()
    old_episode, old_root = dict(s.episode), s.resource
    s.event('SessionEnd')
    owner = owner_record(s)

    # Prove the real public entry before importing the scheduling boundary.
    from poise.infrastructure.document_intake.resources import CoordinationStore
    from poise.infrastructure.clock import SystemClock
    ordinary_gate = CoordinationStore.locked
    interleaving = {}

    @contextmanager
    def delayed_gate(store, deadline=None):
        if store.root == owner.parent and not interleaving:
            interleaving['entered'] = True
            monkeypatch.setattr(CoordinationStore, 'locked', ordinary_gate)
            # R2 really cleans A, then the SDK really starts B for the caller.
            code, cleaned = s.invoke({'operation': 'recover'})
            assert code == 0 and cleaned['status'] == 'cleaned' and not old_root.exists()
            s.event('SessionStart')
            s.ready()
            s.row(s.read(), text=spec['selected_content'], selection='lines:2-3',
                  classification='first_read')
            assert s.episode['start_event_id'] > old_episode['start_event_id']
            interleaving['episode'] = dict(s.episode)
            interleaving['resource'] = s.resource
            interleaving['owner_bytes'] = owner.read_bytes()
            interleaving['tree'] = files(s.resource)
            interleaving['rows'] = s.metadata()
        with ordinary_gate(store, deadline):
            yield

    monkeypatch.setattr(CoordinationStore, 'locked', delayed_gate)
    clock = SystemClock()
    started = clock.observe()
    actual = s.transport(monkeypatch, {'operation': 'recover'})
    finished = clock.observe()
    assert finished.comparison_domain == started.comparison_domain
    elapsed_ns = finished.monotonic_ns - started.monotonic_ns
    assert 0 <= elapsed_ns < spec['coordination']['recovery_budget_seconds'] * 1_000_000_000
    assert interleaving.get('entered') and interleaving['episode'] == s.episode
    assert_response(s, actual, 'stale_recovery', 1, episode=old_episode, resource=old_root)
    # Check preservation before the subsequent legitimate read adds its event/output.
    assert owner.read_bytes() == interleaving['owner_bytes']
    assert s.resource == interleaving['resource'] and s.resource.exists()
    assert files(s.resource) == interleaving['tree']
    assert s.metadata() == interleaving['rows']
    assert_response(s, s.raw({'operation': 'context'}), 'valid_context', 0)
    s.row(s.read(), text=spec['selected_content'], selection='lines:2-3',
          classification='exact_repeat', status='unchanged',
          summary=fixture('context-sequences.json')['repeat'])
    s.event('SessionEnd')
    code, cleaned = s.invoke({'operation': 'recover'})
    assert code == 0 and cleaned['status'] == 'cleaned' and not s.resource.exists()
    code, repeated = s.invoke({'operation': 'recover'})
    assert code == 0 and repeated['status'] == 'already_absent'
