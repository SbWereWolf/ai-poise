"""Canonical public rework and exact own presented-result provenance."""
import pytest

from lifecycle_checks import DATA, Scenario, facts


@pytest.fixture
def scenario(tmp_path):
    return Scenario(tmp_path)


def test_same_stage_rework_retains_contract_and_counts_actual_visits(scenario):
    scenario.presented()
    old = scenario.execution()
    records = scenario.records()
    result = scenario.act('rework', target_stage_id='write', reason='Уточнить текущий результат.',
                          entry_tree=scenario.baseline)
    for key, value in DATA['expected']['same_rework_state'].items():
        assert result[key] == value
    assert result['generation'] == 1 and result['contract_revision'] == old['contract_revision']
    assert all(record in scenario.records() for record in records)
    context, candidate, gate = scenario.gate()
    assert gate['iteration'] == 2
    scenario.act('present', context_id=context, candidate_id=candidate)
    result = scenario.act('rework', target_stage_id='write', reason='Ещё один проход.',
                          entry_tree=scenario.baseline)
    assert result['iteration'] == 3
    scenario.preserved()


def test_new_target_starts_its_own_iteration_and_records_actual_actor(scenario):
    scenario.presented()
    response = scenario.act('rework', actor_id='reviewer-B', target_stage_id='review',
                            reason='Проверить предъявленный результат.', entry_tree=scenario.baseline)
    for key, value in DATA['expected']['review_rework_state'].items():
        assert response[key] == value
    context, candidate, _ = scenario.gate()
    scenario.act('present', actor_id='reviewer-B', context_id=context, candidate_id=candidate)
    back = scenario.act('rework', target_stage_id='write', reason='Вернуться к автору.',
                        entry_tree=scenario.baseline)
    assert back['iteration'] == 2
    history = facts(scenario.lifecycle()['history'])
    recorded = next(item for item in history if item['action'] == 'rework'
                    and item['target_stage_id'] == 'review')
    assert recorded['actor_id'] == 'reviewer-B' and recorded['role_id'] == 'reviewer'
    context2, candidate2, _ = scenario.gate()
    scenario.act('present', context_id=context2, candidate_id=candidate2)
    revisit = scenario.act('rework', target_stage_id='review', reason='Второй проход проверки.',
                           entry_tree=scenario.baseline)
    assert revisit['iteration'] == 2
    scenario.preserved()


@pytest.mark.parametrize('state', ['draft', 'ready', 'active'])
def test_rework_needs_actual_presented_state(scenario, state):
    if state == 'draft':
        # A separate public positive control proves the new action exists, without
        # consuming this subject's draft precondition.
        Scenario(scenario.root / 'control').presented()
    else:
        scenario.freeze()
        if state == 'active':
            scenario.enter()
    scenario.reject(scenario.packet('rework', target_stage_id='write', reason='Проверка состояния.',
                                    entry_tree=scenario.baseline), 'state')


def test_undeclared_rework_target_preserves_presented_result(scenario):
    scenario.presented()
    scenario.reject(scenario.packet('rework', target_stage_id='missing-stage', reason='Проверка цели.',
                                    entry_tree=scenario.baseline), 'target')
    assert scenario.act('rework', target_stage_id='write', reason='Правильная цель.',
                        entry_tree=scenario.baseline)['iteration'] == 2


@pytest.mark.parametrize('wrong', ['rejected', 'stage', 'iteration'])
def test_present_requires_exact_accepted_current_result(scenario, wrong):
    scenario.prepared()
    if wrong == 'rejected':
        context, candidate, gate = scenario.gate(report='')
        assert gate['accepted'] is False
    elif wrong == 'stage':
        context, candidate, _ = scenario.gate(stage='review')
    else:
        context, candidate, _ = scenario.gate(iteration=7)
    scenario.reject(scenario.packet('present', context_id=context, candidate_id=candidate),
                    'result' if wrong == 'rejected' else wrong)
    context, candidate, _ = scenario.gate()
    assert scenario.act('present', context_id=context, candidate_id=candidate)['state'] == 'verified'


def test_previous_iteration_result_cannot_present_reworked_stage(scenario):
    scenario.prepared()
    context, candidate, _ = scenario.gate()
    scenario.act('present', context_id=context, candidate_id=candidate)
    scenario.act('rework', target_stage_id='write', reason='Новый проход.', entry_tree=scenario.baseline)
    scenario.reject(scenario.packet('present', context_id=context, candidate_id=candidate), 'iteration')
    context2, candidate2, _ = scenario.gate()
    assert scenario.act('present', context_id=context2, candidate_id=candidate2)['iteration'] == 2


def test_previous_generation_result_cannot_present_restarted_execution(scenario):
    scenario.prepared()
    context, candidate, _ = scenario.gate()
    scenario.act('present', context_id=context, candidate_id=candidate)
    scenario.proposal()
    approval = scenario.approve()
    scenario.act('restart', proposal_id='proposal-1', approval_id=approval)
    scenario.freeze()
    scenario.enter()
    scenario.reject(scenario.packet('present', context_id=context, candidate_id=candidate), 'result')
    context2, candidate2, _ = scenario.gate()
    assert scenario.act('present', context_id=context2, candidate_id=candidate2)['generation'] == 2


def test_wrong_actor_role_preserves_actual_stage(scenario):
    scenario.freeze()
    scenario.reject(scenario.packet('enter', actor_id='reviewer-B', entry_tree=scenario.baseline), 'role')
    scenario.enter()
    context, candidate, _ = scenario.gate()
    scenario.reject(scenario.packet('present', actor_id='reviewer-B', context_id=context,
                                    candidate_id=candidate), 'role')
    scenario.act('present', context_id=context, candidate_id=candidate)
    scenario.preserved()


def test_physical_oracle_detects_branch_change_at_identical_head_and_wip(scenario):
    before = scenario.physical()
    assert before['branch'] == scenario.workspace_response['workspace']['branch']
    scenario.git('checkout', '-b', 't053-oracle-branch')
    changed = scenario.physical()
    assert changed == dict(before, branch='t053-oracle-branch')
    assert changed != before
    scenario.git('checkout', before['branch'])
    assert scenario.physical() == before
