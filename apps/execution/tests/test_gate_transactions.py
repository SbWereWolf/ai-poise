"""Replay-first owner ledger and real SQLite late-failure rollback."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import pytest
from gate_checks import (arranged, assert_same, current, database, declared, evaluated, fixture,
                         invoke, next_packet, own_rows, packet, query, ready, refusal_unchanged,
                         result_query, state)
from process_checks import store


@pytest.mark.parametrize('accepted', [True, False])
def test_exact_evaluate_replay_uses_saved_outcome_before_live_inputs_or_revision(tmp_path, accepted):
    config, mapping = ready(tmp_path)
    value = packet('evaluate', mapping, files=[mapping['@FILE']] if accepted else [])
    first = evaluated(config, value, accepted=accepted)
    attempt = fixture('attempt')
    store(config).write_records(request_id='later', expected_revision=4, records=[attempt])
    Path(mapping['@FILE']).unlink()
    current(config).unlink()
    before = state(config)
    code, repeated = invoke(config, value)
    assert code == (0 if accepted else 2), repeated
    expected = deepcopy(first)
    expected['replayed'] = True
    assert_same(repeated, expected)
    assert_same(state(config), before)


def test_declare_replay_precedes_current_revision_and_missing_live_process(tmp_path):
    config, mapping = arranged(tmp_path)
    value = packet('declare', mapping)
    first = declared(config, mapping, value)
    store(config).write_records(request_id='later', expected_revision=3, records=[fixture('attempt')])
    current(config).unlink()
    before = state(config)
    code, repeated = invoke(config, value)
    assert code == 0, repeated
    expected = deepcopy(first)
    expected['replayed'] = True
    assert_same(repeated, expected)
    assert_same(state(config), before)


@pytest.mark.parametrize('case', ['request_conflict', 'stale_revision', 'candidate_conflict', 'foreign_ledger'])
def test_conflicts_refuse_without_partial_business_or_request_writes(tmp_path, case):
    config, mapping = ready(tmp_path)
    original = packet('evaluate', mapping)
    first = evaluated(config, original, accepted=True)
    value = next_packet(mapping, first)
    if case == 'request_conflict':
        value['input']['request_id'] = 'evaluate-1'
    elif case == 'stale_revision':
        value['input']['expected_revision'] = 3
    elif case == 'candidate_conflict':
        value['input']['candidate_id'] = 'candidate-1'
        value['input']['sections']['report'] = 'changed'
    else:
        value['input']['request_id'] = 'setup-1'
    refusal_unchanged(config, value)


def test_same_candidate_new_request_returns_saved_decision_while_new_candidate_reobserves(tmp_path):
    config, mapping = ready(tmp_path)
    value = packet('evaluate', mapping)
    first = evaluated(config, value, accepted=True)
    Path(mapping['@FILE']).unlink()
    same = deepcopy(value)
    same['input'].update(request_id='same-candidate', expected_revision=4)
    second = evaluated(config, same, accepted=True, assessment=fixture('assessment'))
    assert_same(second['result_id'], first['result_id'])
    assert len(own_rows(config, 'gate_candidate')) == len(own_rows(config, 'stage_result')) == 1
    fresh = next_packet(mapping, second, files=[])
    evaluated(config, fresh, accepted=False)
    assert len(own_rows(config, 'gate_candidate')) == 2


@pytest.mark.parametrize('field', ['action', 'request_id', 'expected_revision', 'task_id', 'generation',
                                  'context_id', 'candidate_id', 'stage_id', 'iteration', 'sections', 'trace', 'files'])
def test_missing_evaluate_fields_are_protocol_refusals_not_negative_candidates(tmp_path, field):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    value = next_packet(mapping, first)
    del value['input'][field]
    refusal_unchanged(config, value)


@pytest.mark.parametrize('field,bad', [
    ('task_id', '0099'), ('generation', 2), ('generation', True), ('iteration', 0), ('iteration', False),
    ('context_id', 'absent'), ('candidate_id', []), ('stage_id', 'absent'), ('stage_id', {}),
    ('sections', []), ('trace', []), ('files', None), ('files', [1]), ('files', ['relative.txt']),
    ('expected_revision', True), ('request_id', None)])
def test_evaluate_types_and_selectors_refuse_with_no_candidate_effect(tmp_path, field, bad):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    value = next_packet(mapping, first)
    value['input'][field] = bad
    refusal_unchanged(config, value)


@pytest.mark.parametrize('action', ['declare', 'evaluate'])
def test_actual_late_insert_failure_rolls_back_prefix_request_revision_and_chain(tmp_path, action):
    config, mapping = arranged(tmp_path)
    if action == 'evaluate':
        declared(config, mapping)
    value = packet(action, mapping)
    db_path = database(config)
    # Trigger predicate can fire only after the owning operation inserted its
    # real business prefix in this same transaction. RAISE text proves it fired.
    with sqlite3.connect(db_path) as db:
        if action == 'declare':
            db.executescript(fixture('declare_abort_sql'))
        else:
            db.executescript(fixture('evaluate_abort_sql'))
    before = state(config)
    code, outcome = invoke(config, value)
    assert code == 2 and outcome['status'] == 'rejected', outcome
    assert 'test-owned-after-written-prefix' in outcome['reason'], outcome
    assert_same(state(config), before)
    with sqlite3.connect(db_path) as db:
        db.execute('DROP TRIGGER gate_abort')
    if action == 'declare':
        declared(config, mapping, value)
    else:
        evaluated(config, value, accepted=True, assessment=fixture('assessment'))


@pytest.mark.parametrize('case', ['section_not_text', 'trace_route_not_object', 'trace_point_list',
                                  'record_field_not_text', 'files_object', 'generation_zero',
                                  'iteration_text', 'extra_command_field'])
def test_nested_wire_shape_errors_do_not_become_saved_domain_results(tmp_path, case):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    value = next_packet(mapping, first)
    body = value['input']
    if case == 'section_not_text':
        body['sections']['report'] = True
    elif case == 'trace_route_not_object':
        body['trace']['proof'] = []
    elif case == 'trace_point_list':
        body['trace']['proof']['why'] = []
    elif case == 'record_field_not_text':
        body['trace']['proof']['decision']['note'] = False
    elif case == 'files_object':
        body['files'] = {}
    elif case == 'generation_zero':
        body['generation'] = 0
    elif case == 'iteration_text':
        body['iteration'] = '1'
    else:
        body['passed'] = True
    refusal_unchanged(config, value)
