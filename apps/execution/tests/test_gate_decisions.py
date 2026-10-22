"""Own decisions and saved provenance distinguish facts from accepted results."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import pytest
from gate_checks import (assert_same, bind, context_query, database, evaluated, fixture,
                         invoke, negative_rows, next_packet, original_rows, own_rows, packet,
                         query, ready, record, result_query, rows)
from process_checks import store


def test_positive_candidate_decision_and_result_have_exact_independent_data(tmp_path):
    config, mapping = ready(tmp_path)
    value = packet('evaluate', mapping)
    response = evaluated(config, value, accepted=True, assessment=fixture('assessment'))
    result = query(config, result_query())['results'][0]
    assert set(result) == {'id', 'kind', 'candidate', 'decision', 'accepted_result'}
    assert_same(result['id'], 'result')
    assert_same(result['kind'], 'gate_result')
    values = dict(mapping, **{'@RESULT': response['result_id']})
    record(result['candidate'], 'gate_candidate', bind(fixture('candidate_data'), values))
    record(result['decision'], 'gate_decision', bind(fixture('decision_data'), values))
    record(result['accepted_result'], 'stage_result', bind(fixture('accepted_data'), values))
    assert_same(result['accepted_result']['id'], response['result_id'])
    assert_same(own_rows(config, 'gate_candidate'), [result['candidate']])
    assert_same(own_rows(config, 'gate_decision'), [result['decision']])
    assert_same(own_rows(config, 'stage_result'), [result['accepted_result']])
    assert_same(result['candidate']['data']['submitted'],
                {key: value['input'][key] for key in ('sections', 'trace', 'files')})


def test_negative_saved_query_retains_matching_decision_and_never_an_accepted_result(tmp_path):
    config, mapping = ready(tmp_path)
    value = packet('evaluate', mapping, files=[])
    response = evaluated(config, value, accepted=False)
    saved = query(config, result_query())['results'][0]
    assert_same(saved['accepted_result'], None)
    assert_same(saved['decision']['data']['assessment'], response['assessment'])
    assert_same(saved['decision']['data']['provenance'], bind(fixture('provenance'), mapping))
    assert_same(saved['candidate']['data']['files'], [])
    negative_rows(config)
    assert_same(query(config, result_query())['results'][0], saved)


def test_successful_actual_child_command_is_a_fact_and_does_not_satisfy_expectation(tmp_path):
    config, mapping = ready(tmp_path)
    before_execution = original_rows(database(config))['executions']
    child = subprocess.run([sys.executable, '-c', 'raise SystemExit(0)'], capture_output=True)
    assert child.returncode == 0 and child.stdout == child.stderr == b''
    attempt = fixture('attempt')
    store(config).write_records(request_id='child-observation', expected_revision=3, records=[attempt])
    assert_same(own_rows(config, 'attempt'), [attempt])
    evaluated(config, packet('evaluate', mapping, expected_revision=4, files=[]), accepted=False)
    negative_rows(config)
    assert_same(original_rows(database(config))['executions'], before_execution)


@pytest.mark.parametrize('kind', ['gate_context', 'gate_candidate', 'gate_decision', 'stage_result'])
def test_generic_fact_writer_does_not_produce_internal_acceptance_records(tmp_path, kind):
    config, mapping = ready(tmp_path)
    good = evaluated(config, packet('evaluate', mapping), accepted=True)
    before = rows(config)
    fake = fixture('attempt')
    fake['kind'] = kind
    owner = store(config)
    with pytest.raises(Exception) as caught:
        owner.write_records(request_id='not-an-acceptance', expected_revision=good['revision'], records=[fake])
    assert 'kind' in str(caught.value).lower()
    assert_same(rows(config), before)


def test_context_and_candidate_queries_are_isolated_ordered_and_saved(tmp_path):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    result = query(config, result_query(), context_query(), result_query('absent', identity='none'),
                   result_query(context='absent', identity='other-context'),
                   result_query(generation=2, identity='other-generation'),
                   {'id': 'old', 'kind': 'execution', 'task_id': '0042'})
    assert_same(result['revision'], first['revision'])
    assert [entry['id'] for entry in result['results']] == ['result', 'context', 'none', 'other-context', 'other-generation', 'old']
    for entry in result['results'][2:5]:
        assert_same(entry['candidate'], None)
        assert_same(entry['decision'], None)
        assert_same(entry['accepted_result'], None)
    assert_same(result['results'][-1]['value']['state'], 'draft')
    assert_same(result['results'][-1]['value']['current_stage'], None)
    assert_same(result['results'][-1]['value']['iteration'], 1)
    assert_same(result['results'][-1]['value']['generation'], 1)
    Path(mapping['@FILE']).unlink()
    assert_same(query(config, result_query())['results'][0], result['results'][0])


@pytest.mark.parametrize('case', ['missing_context', 'bad_generation', 'bad_candidate', 'extra_field'])
def test_malformed_gate_query_selectors_leave_all_saved_data_unchanged(tmp_path, case):
    from gate_checks import refusal_unchanged
    config, mapping = ready(tmp_path)
    evaluated(config, packet('evaluate', mapping), accepted=True)
    item = result_query()
    if case == 'missing_context':
        del item['context_id']
    elif case == 'bad_generation':
        item['generation'] = True
    elif case == 'bad_candidate':
        item['candidate_id'] = []
    else:
        item['accepted'] = True
    refusal_unchanged(config, {'schema': 'stride-execution/v1', 'operation': 'query',
                               'input': {'queries': [item]}})


def test_existing_populated_contexts_have_independent_snapshots_files_ids_and_history(tmp_path):
    # F02: both contexts actually exist and share candidate IDs. Neither an
    # absent-context lookup nor different candidate IDs can hide a shared chain.
    from gate_checks import declared
    config, mapping = ready(tmp_path)
    original = packet('evaluate', mapping)
    first = evaluated(config, original, accepted=True, assessment=fixture('assessment'))
    one_before = query(config, result_query())['results'][0]
    declare_two = packet('declare', mapping, context_id='context-2', request_id='declare-two',
                         expected_revision=first['revision'])
    declared(config, mapping, declare_two, revision=5)
    empty_two = packet('evaluate', mapping, context_id='context-2', request_id='two-empty',
                       expected_revision=5, trace={}, files=[])
    empty_two['input']['sections'] = fixture('context_two_sections')
    second = evaluated(config, empty_two, accepted=False, assessment=fixture('context_two_empty_assessment'))
    empty_saved = query(config, result_query(context='context-2'))['results'][0]
    assert_same(empty_saved['candidate']['data']['snapshot'], fixture('context_two_empty_snapshot'))
    assert_same(empty_saved['candidate']['data']['files'], [])
    assert_same(empty_saved['candidate']['data']['catalogue'], [])
    assert_same(empty_saved['accepted_result'], None)
    provenance = bind(fixture('provenance'), mapping)
    provenance.update(context_id='context-2', request_id='two-empty')
    assert_same(empty_saved['decision']['data']['provenance'], provenance)
    # A new evaluation in context1 must retain its own existing trace/catalogue.
    one_next = next_packet(mapping, second, trace={}, files=[])
    third = evaluated(config, one_next, accepted=True, assessment=fixture('assessment'))
    other_file = tmp_path / 'state/files/task/outputs/other.txt'
    other_file.write_text('context two file')
    filled_two = next_packet(mapping, third, context_id='context-2', request_id='two-filled',
                             sections=fixture('context_two_sections'), trace=fixture('context_two_trace'),
                             files=[str(other_file)])
    fourth = evaluated(config, filled_two, accepted=True, assessment=fixture('assessment'))
    readback = query(config, result_query(), result_query(context='context-2', identity='two-empty'),
                    result_query('candidate-2', context='context-2', identity='two-filled'))
    assert_same(readback['results'][0], one_before)
    assert_same(readback['results'][1]['candidate'], empty_saved['candidate'])
    assert_same(readback['results'][1]['decision'], empty_saved['decision'])
    two = readback['results'][2]
    assert_same(two['candidate']['data']['snapshot'], fixture('context_two_filled_snapshot'))
    assert_same(two['candidate']['data']['files'], fixture('context_two_facts'))
    assert_same(two['candidate']['data']['catalogue'], fixture('context_two_facts'))
    provenance.update(candidate_id='candidate-2', request_id='two-filled')
    assert_same(two['decision']['data']['provenance'], provenance)
    assert_same(two['accepted_result']['data']['provenance'], provenance)
    assert_same(two['accepted_result']['data']['snapshot'], fixture('context_two_filled_snapshot'))
    # Saved and fresh context1 outcomes still refer only to context1 facts.
    code, replay = invoke(config, original)
    assert code == 0, replay
    expected = deepcopy(first)
    expected['replayed'] = True
    assert_same(replay, expected)
    code, replay_two = invoke(config, empty_two)
    assert code == 2, replay_two
    expected_two = deepcopy(second)
    expected_two['replayed'] = True
    assert_same(replay_two, expected_two)
    last = next_packet(mapping, fourth, 'candidate-3', trace={}, files=[])
    evaluated(config, last, accepted=True, assessment=fixture('assessment'))
    one_after = query(config, result_query('candidate-3'))['results'][0]
    assert_same(one_after['candidate']['data']['snapshot'], fixture('snapshot'))
    assert_same(one_after['candidate']['data']['files'], fixture('facts'))
    assert_same(one_after['candidate']['data']['catalogue'], fixture('facts'))
    assert_same(query(config, result_query())['results'][0], one_before)
    # Reopening a real CLI process preserves both same-ID candidates separately.
    assert_same(query(config, result_query('candidate-2', context='context-2', identity='two-filled'))['results'][0], two)
