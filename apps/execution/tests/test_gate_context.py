"""Original Task context bound to own frozen process/contract, via real CLI."""
from copy import deepcopy
import json
import pytest
from gate_checks import (arranged, bind, context_query, current, database, declared, fixture,
                         invoke, own_rows, packet, query, ready, record, refusal_unchanged,
                         rows, state, write_json, assert_same)


@pytest.mark.parametrize('absolute', [False, True])
def test_context_retains_exact_task_input_and_frozen_references(tmp_path, absolute):
    config, mapping = arranged(tmp_path, absolute=absolute)
    value = packet('declare', mapping)
    declared(config, mapping, value)
    expected = bind(fixture('context_data'), mapping)
    saved = query(config, context_query())['results'][0]
    assert set(saved) == {'id', 'kind', 'context'}
    assert_same(saved['id'], 'context')
    assert_same(saved['kind'], 'gate_context')
    record(saved['context'], 'gate_context', expected)
    assert_same(own_rows(config, 'gate_context'), [saved['context']])
    current(config).unlink()
    (tmp_path / 'state/templates/starter.json').unlink()
    assert_same(query(config, context_query())['results'][0], saved)


def test_explicit_empty_context_works_and_missing_is_not_empty(tmp_path):
    definition = fixture('definition')
    definition['content_contract'] = fixture('empty')
    for stage in definition['stages']:
        stage['artifact_requirements'] = []
    config, mapping = arranged(tmp_path, definition=definition)
    value = packet('declare', mapping, task_content=fixture('empty'), requirement_ids=[])
    for stage in value['input']['stage_contracts']:
        stage['entry_requirements'] = []
        stage['exit_requirements'] = []
    missing = deepcopy(value)
    del missing['input']['task_content']
    refusal_unchanged(config, missing, 'task_content', 'field')
    declared(config, mapping, value)


@pytest.mark.parametrize('field', ['action', 'request_id', 'expected_revision', 'task_id', 'generation',
                                  'context_id', 'contract_revision', 'requirement_ids', 'task_content', 'stage_contracts'])
def test_missing_declare_field_is_unchanged_refusal(tmp_path, field):
    config, mapping = arranged(tmp_path)
    good = packet('declare', mapping)
    # A positive declaration proves this is not a generic unsupported-operation refusal.
    declared(config, mapping, good)
    value = packet('declare', mapping, request_id='malformed', expected_revision=3, context_id='other')
    del value['input'][field]
    refusal_unchanged(config, value, field, 'field', 'action')


@pytest.mark.parametrize('field,bad', [
    ('request_id', []), ('task_id', {}), ('context_id', True), ('generation', True),
    ('generation', 2), ('expected_revision', False), ('contract_revision', 'different'),
    ('requirement_ids', None), ('requirement_ids', ['R1', 'R1']),
    ('task_content', []), ('stage_contracts', {}), ('task_id', '0099')])
def test_declare_types_or_saved_binding_mismatch_are_atomic(tmp_path, field, bad):
    config, mapping = arranged(tmp_path)
    declared(config, mapping)
    value = packet('declare', mapping, request_id='bad', expected_revision=3, context_id='other')
    value['input'][field] = bad
    refusal_unchanged(config, value)


@pytest.mark.parametrize('case', ['order', 'coverage', 'unknown_ref', 'wrong_phase', 'readonly',
                                  'unknown_method', 'unknown_requirement', 'duplicate_rule',
                                  'nested_type', 'wrong_source', 'early_arrival'])
def test_original_layer_and_stage_relationships_are_preserved(tmp_path, case):
    config, mapping = arranged(tmp_path)
    declared(config, mapping)
    value = packet('declare', mapping, request_id='bad', expected_revision=3, context_id='other')
    body = value['input']
    if case == 'order':
        body['stage_contracts'].reverse()
    elif case == 'coverage':
        body['stage_contracts'].pop()
    elif case == 'unknown_ref':
        body['stage_contracts'][0]['exit_requirements'].append('missing')
    elif case == 'wrong_phase':
        body['stage_contracts'][0]['entry_requirements'] = ['t-file']
    elif case == 'readonly':
        body['stage_contracts'][1]['allowed_paths'] = ['outputs/**']
    elif case == 'unknown_method':
        # A declared trace method value is checked at evaluation, not invented here.
        body['task_content']['routes'][0]['points'][1]['kind'] = 'unknown'
    elif case == 'unknown_requirement':
        body['requirement_ids'] = ['R2']
    elif case == 'duplicate_rule':
        body['task_content']['requirements'].append(deepcopy(body['task_content']['requirements'][0]))
    elif case == 'nested_type':
        body['stage_contracts'][0]['criteria']['requirements'] = True
    elif case == 'wrong_source':
        body['task_content']['requirements'][-1]['source'] = {'kind': 'stage_output', 'producer_stage': 'review'}
    else:
        rule = body['task_content']['requirements'][-1]
        rule.update(phase='pre', source={'kind': 'declared_arrival', 'arrival_stage': 'review'})
        body['stage_contracts'][0]['exit_requirements'].remove('t-file')
        body['stage_contracts'][0]['entry_requirements'].append('t-file')
    refusal_unchanged(config, value)


def test_context_identity_is_immutable_and_distinct_snapshots_are_explicit(tmp_path):
    config, mapping = ready(tmp_path)
    same = packet('declare', mapping, request_id='declare-again', expected_revision=3)
    declared(config, mapping, same, revision=4, changed=False)
    different = packet('declare', mapping, request_id='conflict', expected_revision=4)
    different['input']['stage_contracts'][0]['criteria']['requirements'] = ['Уточнённое условие.']
    refusal_unchanged(config, different)
    different['input'].update(context_id='context-2', request_id='new-context')
    declared(config, mapping, different, revision=5)
    assert len(own_rows(config, 'gate_context')) == 2


def test_absent_gate_config_preserves_existing_api_and_has_actionable_gate_refusal(tmp_path):
    config, mapping = arranged(tmp_path, gates=False)
    result = query(config, {'id': 'old', 'kind': 'execution', 'task_id': '0042'})
    assert_same(result['results'][0]['value']['state'], 'draft')
    refusal_unchanged(config, packet('declare', mapping), 'gates', 'config')


def test_core_only_setup_stays_usable_and_missing_frozen_process_is_explicit(tmp_path):
    from process_checks import installation, applied
    config = installation(tmp_path, process=False)
    data = json.loads(config.read_text())
    data['gates'] = {'roots': {'runtime': 'files/runtime', 'task': 'files/task', 'sprint': 'files/sprint'}}
    write_json(config, data)
    contract = fixture('contract')
    contract['workspace_id'] = 'workspace-1'
    contract['process_ref']['revision'] = 'selected-process'
    setup = fixture('setup_packet')
    setup['input']['expected_revision'] = 0
    setup['input']['operations'][0]['contract'] = contract
    response = applied(invoke(config, setup))
    mapping = {'@CONTRACT': response['results'][0]['contract_revision']}
    query(config, {'id': 'old', 'kind': 'execution', 'task_id': '0042'})
    refusal_unchanged(config, packet('declare', mapping, expected_revision=1), 'process', 'snapshot', 'процесс')
