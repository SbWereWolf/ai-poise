"""R4: real publication, configured readback, honest public-stream limitations."""
import copy
import json
import os
import sys

import pytest
pytest.register_assert_rewrite('erp_support')
from erp_support import (
    CONTRACT, FIXTURES, action, arrange_target_bytes, bootstrap,
    bootstrap_configuration, observations, observe_exact, read_json,
    require_input, selected_catalog, source_override, targets, values,
    wire_configuration, write_json,
)

pytest_plugins = ['erp_support']
TARGET_IDS = [item['id'] for item in CONTRACT['configuration_targets']]


@pytest.mark.parametrize('identifier', TARGET_IDS)
@pytest.mark.parametrize('previous', [False, True], ids=['initial', 'repeat'])
def test_configuration_actions_publish_exact_test_owned_bytes(erp_deployed, identifier, previous):
    case = erp_deployed
    target = next(t for t in targets(case) if t['id'] == identifier)
    target['path'].parent.mkdir(parents=True, exist_ok=True)
    if previous:
        target['path'].write_bytes((FIXTURES / 'erp-json-old.json').read_bytes())
    overrides = source_override(case, target)
    initial, state = action(case, 'erp-' + identifier, 'check', overrides)
    assert initial.returncode == 0
    assert state['state'] == ('invalid' if previous else 'missing')
    mutation, outcome = action(case, 'erp-' + identifier,
                               'repair' if previous else 'install', overrides)
    assert mutation.returncode == 0 and outcome['outcome'] == 'completed', (
        mutation.stdout, mutation.stderr)
    expected = (FIXTURES / 'erp-json-new.json').read_bytes()
    assert target['path'].read_bytes() == expected
    final, state = action(case, 'erp-' + identifier, 'check', overrides)
    assert final.returncode == 0 and state['state'] == 'satisfied'
    _, report = bootstrap(case, configured=wire_configuration(case))
    record = observations(report)[identifier]
    observe_exact(record, identifier=identifier, kind=target['kind'], content=expected)


@pytest.mark.parametrize('identifier', TARGET_IDS)
@pytest.mark.parametrize('previous', [False, True], ids=['initial', 'repeat'])
@pytest.mark.parametrize('failure', ['during-write', 'after-publication'])
def test_interrupted_publication_preserves_truthful_whole_state(
        erp_deployed, identifier, previous, failure):
    case = erp_deployed
    arrange_target_bytes(case)
    target = next(t for t in targets(case) if t['id'] == identifier)
    old = (FIXTURES / 'erp-json-old.json').read_bytes()
    if previous:
        target['path'].write_bytes(old)
    else:
        target['path'].unlink()
    overrides = source_override(case, target)
    write_json(case['profile'] / 'values.json', values(case, overrides))
    log = case['root'] / 'mutation-calls.jsonl'
    prefix = [sys.executable, '-B', FIXTURES / 'erp_process_boundary.py', '--log', log]
    prefix += ['--file-size-limit', '64'] if failure == 'during-write' else ['--after-effect-failure']
    catalog = selected_catalog(case, 'erp-' + identifier,
                               role='repair' if previous else 'install', prefix=prefix)
    result, report = bootstrap(case, 'infra', catalog=catalog)
    assert result.returncode != 0 and report['status'] == 'error'
    assert len(log.read_text().splitlines()) == 1, 'No second repair attempt'
    record = observations(report)[identifier]
    if failure == 'after-publication':
        expected = (FIXTURES / 'erp-json-new.json').read_bytes()
    else:
        expected = old if previous else None
    if expected is None:
        assert not target['path'].exists()
        assert record['state'] == 'absent' and record['sha256'] is None
    else:
        assert target['path'].read_bytes() == expected, 'No partially written configuration'
        observe_exact(record, identifier=identifier, kind=target['kind'], content=expected)
    assert report['observations'], 'Keep the original failure diagnosis, not only readback'


@pytest.mark.parametrize('diagnostic', ['', 'ordinary log\n', '{broken json\n', '[REDACTED]\n', 'ambiguous'])
def test_diagnostic_record_never_supplies_configuration_identity(erp_deployed, diagnostic):
    case = erp_deployed
    expected = arrange_target_bytes(case)
    if diagnostic == 'ambiguous':
        path = FIXTURES / 'erp-ambiguous-diagnostics.jsonl'
    else:
        path = case['root'] / 'diagnostic.txt'
        path.write_text(diagnostic, encoding='utf-8')
    result, report = bootstrap(case, configured=wire_configuration(case, stderr=path))
    assert result.returncode == 0 and report['status'] == 'ready'
    actual = observations(report)
    assert set(actual) == set(TARGET_IDS)
    for target in targets(case):
        record = actual[target['id']]
        assert set(record) == {'id', 'kind', 'state', 'sha256', 'cause'}
        observe_exact(record, identifier=target['id'], kind=target['kind'], content=expected)


def test_real_consumer_redaction_is_preserved_and_not_used_as_authority(erp_deployed):
    case = erp_deployed
    expected = arrange_target_bytes(case)
    diagnostic = case['root'] / 'redaction-diagnostic.jsonl'
    diagnostic.write_bytes((FIXTURES / 'erp-redaction-diagnostic.json').read_bytes())
    requirement = {'id': 'wire-fixture'}
    for role in ('check', 'install', 'repair'):
        requirement[role] = {
            'argv': [sys.executable, '-B', str(FIXTURES / 'erp_diagnostic_action.py'),
                     '--id', 'wire-fixture', '--schema', 'fixture/action/v1', '--role', role,
                     '--stderr-file', str(diagnostic)],
            'cwd': str(case['root']), 'env': {}, 'output': {'schema': 'fixture/action/v1'},
            'max_output_bytes': 65536, 'execution_bound_seconds': 30,
        }
    catalog = case['root'] / 'redaction-catalog'
    write_json(catalog / 'catalog.json', {
        'schema': 'environment-maintenance/catalog/v2',
        'parameters': {'token': {'type': 'string', 'default': 'present', 'secret': True}},
        'inherit_env': [], 'requirements': [requirement],
    })
    configured = bootstrap_configuration(case)
    configured['commands']['check'] = [require_input('P003_CONSUMER_PYTHON'), '-I', '-B',
                                       '-m', 'environment_maintenance', 'check',
                                       '--catalog-dir', str(catalog)]
    result, report = bootstrap(case, configured=configured)
    assert result.returncode == 0 and report['status'] == 'ready'
    public = json.loads(report['observations'][0]['stdout'])
    action_result = public['requirements'][0]['initial_check']
    assert action_result['status'] == 'passed' and action_result['capture_complete'] is True
    assert json.loads(action_result['stderr'])['state'] == '[REDACTED]'
    record = observations(report)['cjm-settings']
    observe_exact(record, identifier='cjm-settings', kind='cjm', content=expected)


@pytest.mark.parametrize('failure', ['read', 'stat'])
def test_unreadable_target_is_unavailable_not_invented_absence(erp_deployed, failure):
    assert os.geteuid() != 0, 'Use the declared unprivileged Linux test runtime'
    case = erp_deployed
    arrange_target_bytes(case)
    target = targets(case)[-1]
    protected = target['path'] if failure == 'read' else target['path'].parent
    protected.chmod(0)
    try:
        result, report = bootstrap(case, configured=wire_configuration(case))
    finally:
        protected.chmod(0o600 if failure == 'read' else 0o700)
    assert result.returncode != 0 and report['status'] == 'error'
    record = observations(report)[target['id']]
    assert record['state'] == 'unavailable' and record['sha256'] is None
    assert record['cause'] == 'permission-denied'


def test_confirmed_missing_required_target_prevents_success(erp_deployed):
    case = erp_deployed
    arrange_target_bytes(case)
    target = targets(case)[-1]
    target['path'].unlink()
    result, report = bootstrap(case, configured=wire_configuration(case))
    assert result.returncode != 0 and report['status'] == 'error'
    record = observations(report)[target['id']]
    assert record['state'] == 'absent' and record['sha256'] is None
    assert record['cause'] == 'not-found'


@pytest.mark.parametrize('kind', ['malformed', 'incomplete', 'timeout'])
def test_successful_readback_cannot_promote_failed_command(erp_deployed, kind):
    case = erp_deployed
    expected = arrange_target_bytes(case)
    reply = case['root'] / 'reply.json'
    code = 0
    if kind == 'malformed':
        reply.write_text('not-json\n')
    else:
        data = copy.deepcopy(read_json(FIXTURES / 'erp-ready-result.json'))
        data.update(status='failed', exit_code=4)
        row = data['requirements'][0]
        row['status'] = 'failed'
        row.pop('initial_state')
        row.pop('state')
        row['initial_check'].pop('response')
        row['initial_check'].update(status='protocol_failure', stdout='', stderr='', exit_code=-9,
                                    capture_complete=kind != 'incomplete', timed_out=kind == 'timeout',
                                    error='Test-owned incomplete command observation')
        write_json(reply, data)
        code = 4
    result, report = bootstrap(case, configured=wire_configuration(case, reply=reply, exit_code=code))
    assert result.returncode != 0 and report['status'] == 'error'
    for target in targets(case):
        observe_exact(observations(report)[target['id']], identifier=target['id'],
                      kind=target['kind'], content=expected)
    assert report['observations'][0]['stdout'] == reply.read_text()


@pytest.mark.parametrize('selection', ['empty', 'duplicate'])
def test_missing_or_ambiguous_target_selection_is_not_guessed(erp_deployed, selection):
    case = erp_deployed
    arrange_target_bytes(case)
    configured = wire_configuration(case)
    declared = configured['handler']['options']['configuration_targets']
    configured['handler']['options']['configuration_targets'] = [] if selection == 'empty' else [declared[0], declared[0]]
    result, report = bootstrap(case, configured=configured)
    assert result.returncode != 0 and report['status'] == 'error'
