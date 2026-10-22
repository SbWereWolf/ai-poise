"""Independent section, trace and complete mandatory-gate oracles."""
from copy import deepcopy
import pytest
from gate_checks import (assert_same, evaluated, fixture, negative_rows, next_packet, own_rows,
                         packet, query, ready, result_query)


def test_exact_passing_assessment_and_saved_normalized_state(tmp_path):
    config, mapping = ready(tmp_path)
    evaluated(config, packet('evaluate', mapping), accepted=True, assessment=fixture('assessment'))
    saved = query(config, result_query())['results'][0]
    assert_same(saved['candidate']['data']['snapshot'], fixture('snapshot'))
    assert_same(saved['candidate']['data']['files'], fixture('facts'))
    assert_same(saved['candidate']['data']['catalogue'], fixture('facts'))


@pytest.mark.parametrize('value,state', [('draft', 'populated'), (' draft ', 'template'), ('', 'empty')])
def test_extra_exact_normalization_uses_original_text_and_state(tmp_path, value, state):
    config, mapping = ready(tmp_path)
    candidate = packet('evaluate', mapping)
    candidate['input']['sections']['literal'] = value
    evaluated(config, candidate, accepted=True, assessment=fixture('assessment'))
    sections = query(config, result_query())['results'][0]['candidate']['data']['snapshot']['sections']
    actual = next(s for s in sections if s['name'] == 'literal')
    assert_same(actual, {'name': 'literal', 'content': value, 'state': state})


@pytest.mark.parametrize('report', ['', '   ', 'draft', ' draft '])
def test_required_standard_empty_or_template_saves_negative_preparation(tmp_path, report):
    config, mapping = ready(tmp_path)
    candidate = packet('evaluate', mapping)
    candidate['input']['sections']['report'] = report
    result = evaluated(config, candidate, accepted=False)
    assessment = result['assessment']
    assert_same(assessment['prepared'], False)
    assert type(assessment['preparation_error']) is str and assessment['preparation_error']
    assert_same(assessment['pre'], None)
    assert_same(assessment['post'], None)
    assert_same(assessment['passed'], False)
    negative_rows(config)


@pytest.mark.parametrize('case', ['missing_standard', 'undeclared_extra', 'unknown_route',
                                  'unknown_point', 'unknown_method', 'unknown_record_field',
                                  'nonallowed_record_value', 'unwritable_section'])
def test_well_shaped_domain_submission_failure_is_saved_not_accepted(tmp_path, case):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    candidate = next_packet(mapping, first)
    body = candidate['input']
    if case == 'missing_standard':
        del body['sections']['report']
    elif case == 'undeclared_extra':
        body['sections']['absent'] = 'text'
    elif case == 'unknown_route':
        body['trace']['absent'] = {}
    elif case == 'unknown_point':
        body['trace']['proof']['absent'] = 'text'
    elif case == 'unknown_method':
        body['trace']['proof']['method'] = 'not-declared'
    elif case == 'unknown_record_field':
        body['trace']['proof']['decision']['absent'] = 'text'
    elif case == 'nonallowed_record_value':
        body['trace']['proof']['decision']['verdict'] = 'unknown'
    else:
        body['stage_id'] = 'review'
    result = evaluated(config, candidate, accepted=False)
    assert_same(result['assessment']['prepared'], False)
    assert type(result['assessment']['preparation_error']) is str
    negative_rows(config, 'candidate-2')
    # Failed preparation leaves the previous successfully prepared chain intact.
    good = next_packet(mapping, result, 'candidate-3', trace={})
    evaluated(config, good, accepted=True, assessment=fixture('assessment'))


@pytest.mark.parametrize('case', ['field_equals', 'incomplete_record', 'missing_method', 'missing_why'])
def test_negative_trace_rules_retain_all_later_assessments_and_provenance(tmp_path, case):
    config, mapping = ready(tmp_path)
    candidate = packet('evaluate', mapping)
    expected = fixture('assessment')
    if case == 'field_equals':
        candidate['input']['trace']['proof']['decision']['verdict'] = 'no'
    elif case == 'incomplete_record':
        candidate['input']['trace']['proof']['decision']['note'] = ''
    elif case == 'missing_method':
        candidate['input']['trace']['proof']['method'] = None
    else:
        candidate['input']['trace']['proof']['why'] = ''
    result = evaluated(config, candidate, accepted=False)
    post = result['assessment']['post']
    assert [r['id'] for r in post['requirements']] == ['t-report', 't-verdict', 't-coverage', 't-file']
    expected['passed'] = expected['post']['passed'] = False
    if case in ('field_equals', 'incomplete_record'):
        expected['post']['requirements'][1].update(passed=False, detail='точка отсутствует/неполна либо не удовлетворяет field_equals')
    if case != 'field_equals':
        expected['post']['requirements'][2].update(passed=False, detail="uncovered=['R1']; points=['why', 'method', 'decision']")
    assert_same(result['assessment'], expected)
    negative_rows(config)


def test_pre_failure_cannot_be_overridden_by_successful_post(tmp_path):
    config, mapping = ready(tmp_path)
    candidate = packet('evaluate', mapping)
    candidate['input']['sections']['goal-note'] = ' note '
    expected = fixture('assessment')
    expected['passed'] = expected['pre']['passed'] = False
    expected['pre']['requirements'][0].update(passed=False, detail="state=template; требуется ['populated']")
    evaluated(config, candidate, accepted=False, assessment=expected)
    negative_rows(config)


def test_trace_upstream_change_invalidates_only_downstream_and_history_is_retained(tmp_path):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    unchanged = next_packet(mapping, first, trace={'proof': {'why': 'because'}})
    second = evaluated(config, unchanged, accepted=True, assessment=fixture('assessment'))
    changed = next_packet(mapping, second, 'candidate-3', trace={'proof': {'why': 'different'}})
    third = evaluated(config, changed, accepted=False)
    latest = query(config, result_query('candidate-3'))['results'][0]
    assert_same(latest['candidate']['data']['snapshot']['trace'],
                {'proof': {'why': 'different', 'method': None, 'decision': None}})
    original = query(config, result_query())['results'][0]
    assert_same(original['candidate']['data']['snapshot'], fixture('snapshot'))
    repaired = next_packet(mapping, third, 'candidate-4')
    evaluated(config, repaired, accepted=True, assessment=fixture('assessment'))
    assert len(own_rows(config, 'gate_candidate')) == 4


def test_explicit_empty_obligations_produce_explicit_empty_assessments(tmp_path):
    from gate_checks import arranged, declared
    definition = fixture('definition')
    definition['content_contract'] = fixture('empty')
    definition['stages'][0]['artifact_requirements'] = []
    config, mapping = arranged(tmp_path, definition=definition)
    context = packet('declare', mapping, task_content=fixture('empty'), requirement_ids=[])
    for stage in context['input']['stage_contracts']:
        stage['entry_requirements'] = stage['exit_requirements'] = []
    declared(config, mapping, context)
    value = packet('evaluate', mapping, sections={'report': 'done'}, trace={}, files=[])
    response = evaluated(config, value, accepted=True)
    assert_same(response['assessment'], fixture('empty_assessment'))


def test_unselected_rules_and_other_stage_rules_do_not_override_explicit_binding(tmp_path):
    from gate_checks import arranged, declared
    config, mapping = arranged(tmp_path)
    context = packet('declare', mapping)
    unselected = deepcopy(context['input']['task_content']['requirements'][1])
    unselected.update(id='not-selected', field_equals={'verdict': 'no'})
    future = deepcopy(context['input']['task_content']['requirements'][0])
    future.update(id='future', stages=['review'], states=['empty'])
    context['input']['task_content']['requirements'].extend([unselected, future])
    declared(config, mapping, context)
    evaluated(config, packet('evaluate', mapping), accepted=True, assessment=fixture('assessment'))


def test_standard_exact_normalization_and_empty_phase_bindings(tmp_path):
    config, mapping = ready(tmp_path)
    value = packet('evaluate', mapping, stage_id='review', sections={'report': ' draft '}, trace={}, files=[])
    response = evaluated(config, value, accepted=True)
    assert_same(response['assessment']['pre']['requirements'], [])
    assert_same(response['assessment']['post']['requirements'], [])
    snapshot = query(config, result_query())['results'][0]['candidate']['data']['snapshot']
    assert_same(snapshot['sections'], [{'name': 'report', 'content': ' draft ', 'state': 'populated'}])
