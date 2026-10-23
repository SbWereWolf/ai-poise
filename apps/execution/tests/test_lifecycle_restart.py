"""Real public lifecycle authority/revision/preservation; independent literal effects."""
from copy import deepcopy
import json
import sqlite3

import pytest

from lifecycle_checks import DATA, Scenario, invoke, facts, bind


@pytest.fixture
def scenario(tmp_path):
    return Scenario(tmp_path)


def test_existing_public_producers_and_setup_are_valid(scenario):
    assert scenario.execution() == bind(DATA['expected']['execution_after_setup'], {
        '@CONTRACT': scenario.contract_revision,
        '@WORKSPACE': scenario.workspace_response['workspace']['workspace_id']})
    assert scenario.initial_snapshot['statement']['goal'] == DATA['goal']
    assert scenario.initial_snapshot['snapshot_revision'] == 2
    scenario.preserved()


def test_public_freeze_enter_and_present_use_real_gate(scenario):
    first = scenario.freeze()
    for key, value in DATA['expected']['first_state'].items():
        assert first[key] == value
    assert scenario.lifecycle()['registered_goal'] == DATA['goal']
    assert scenario.lifecycle()['requirements_snapshot'] == scenario.initial_snapshot
    entered = scenario.enter()
    for key, value in DATA['expected']['entered_state'].items():
        assert entered[key] == value
    context, candidate, gate = scenario.gate()
    assert gate['accepted'] is True and scenario.execution()['state'] == 'active'
    presented = scenario.act('present', context_id=context, candidate_id=candidate)
    for key, value in DATA['expected']['presented_state'].items():
        assert presented[key] == value
    assert scenario.lifecycle()['entry_tree'] == scenario.baseline
    scenario.preserved()


def test_reviewer_agreement_executor_restart_preserves_history_and_real_wip(scenario):
    scenario.presented()
    before = scenario.execution()
    old_records = scenario.records()
    snapshot = scenario.publish('Согласованные новые ожидания.')
    scenario.neighbor_before = scenario.neighbors()  # Producer preparation finished.
    proposed = scenario.proposal(snapshot)
    assert scenario.execution() == before
    approval = scenario.approve()
    assert scenario.execution() == before
    packet = scenario.packet('restart', proposal_id='proposal-1', approval_id=approval)
    response = scenario.run(packet)
    assert response['generation'] == 2 and response['contract_revision'] != before['contract_revision']
    for key, value in DATA['expected']['restarted_state'].items():
        assert response[key] == value
    scenario.replay(packet, response)
    assert all(record in scenario.records() for record in old_records)
    lifecycle = scenario.lifecycle()
    assert lifecycle['registered_goal'] == DATA['goal']
    assert lifecycle['requirements_snapshot'] == snapshot
    own_history = scenario.query('restart_history')['result']
    history = facts(own_history)
    decision = next(item for item in history if item['action'] == 'decide_restart')
    assert decision['actor_id'] == 'reviewer-B' and decision['role_id'] == 'reviewer'
    assert decision['decision'] == 'approved' and decision['proposal_id'] == 'proposal-1'
    assert decision['approval_id'] == approval
    restarted = next(item for item in history if item['action'] == 'restart')
    assert restarted['actor_id'] == 'executor-A' and restarted['role_id'] == 'writer'
    assert restarted['before_contract'] == scenario.contract
    assert restarted['after_contract'] == proposed
    assert restarted['before_generation'] == 1 and restarted['after_generation'] == 2
    assert any(record['kind'] == 'contract_snapshot' and record['generation'] == 2
               and record['data']['contract'] == proposed for record in scenario.records())
    assert any(record['kind'] == 'contract_snapshot' and record['generation'] == 1
               and record['data']['contract'] == scenario.contract for record in scenario.records())
    assert all(record['generation'] == 1 for record in scenario.records()
               if record['id'] in ('prior-attempt', 'prior-evidence'))
    scenario.freeze(snapshot)
    scenario.enter()
    assert scenario.generation == 2
    scenario.preserved()


@pytest.mark.parametrize('actor', ['executor-A', 'executor-C', 'not-configured'])
def test_only_distinct_configured_approving_role_can_decide(scenario, actor):
    scenario.prepared()
    scenario.proposal()
    scenario.reject(scenario.packet('decide_restart', actor_id=actor, proposal_id='proposal-1',
                                    decision='approved', reason='Проверка роли.'),
                    'actor' if actor == 'not-configured' else 'role')
    # A different actor with the correct role succeeds on the same untouched proposal.
    assert scenario.approve()


def test_rejected_decision_and_generic_history_do_not_authorize_restart(scenario):
    scenario.prepared()
    scenario.proposal()
    approval = scenario.approve('rejected')
    scenario.reject(scenario.packet('restart', proposal_id='proposal-1', approval_id=approval), 'approv')
    scenario.store.write_records(request_id='plain-history', expected_revision=scenario.revision,
        records=[deepcopy(DATA['generic_history_approval'])])
    scenario.revision += 1
    scenario.reject(scenario.packet('restart', proposal_id='proposal-1', approval_id='looks-approved'), 'approv')


@pytest.mark.parametrize('field,value', [('workspace_id', 'different-workspace'),
    ('admission_id', 'different-admission'), ('task_id', '0043'), ('restart_policy_id', 'different-policy')])
def test_proposed_revision_preserves_identity_and_explicit_grant(scenario, field, value):
    scenario.prepared()
    changed = deepcopy(scenario.contract)
    changed[field] = value
    scenario.reject(scenario.packet('propose_restart', proposal_id='bad-proposal', contract=changed,
        requirements_snapshot=scenario.initial_snapshot, reason='Проверка границ.'), field)
    assert scenario.proposal()


def test_actual_published_changed_goal_is_rejected(scenario):
    scenario.prepared()
    changed = scenario.publish('Иные ожидания.', goal='Другая зарегистрированная цель.')
    scenario.neighbor_before = scenario.neighbors()
    proposed = deepcopy(scenario.contract)
    proposed['requirements_snapshot_revision'] = changed['snapshot_revision']
    scenario.reject(scenario.packet('propose_restart', proposal_id='changed-goal', contract=proposed,
        requirements_snapshot=changed, reason='Проверка неизменной цели.'), 'goal')
    scenario.preserved()


@pytest.mark.parametrize('field,value,token', [('expected_revision', 0, 'revision'),
    ('expected_generation', 9, 'generation'), ('expected_contract_revision', 'stale-contract', 'contract')])
def test_stale_inputs_preserve_canonical_state(scenario, field, value, token):
    scenario.prepared()
    scenario.proposal()
    approval = scenario.approve()
    packet = scenario.packet('restart', proposal_id='proposal-1', approval_id=approval)
    packet['input'][field] = value
    scenario.reject(packet, token)
    assert scenario.act('restart', proposal_id='proposal-1', approval_id=approval)['generation'] == 2


@pytest.mark.parametrize('action', ['freeze', 'enter', 'present', 'propose_restart', 'decide_restart', 'restart', 'rework'])
def test_each_public_mutation_has_exact_replay_and_conflict_refusal(scenario, action):
    if action == 'freeze':
        packet = scenario.packet(action, requirements_snapshot=scenario.initial_snapshot)
    else:
        scenario.freeze()
        if action == 'enter':
            packet = scenario.packet(action, entry_tree=scenario.baseline)
        else:
            scenario.enter()
            context, candidate, _ = scenario.gate()
            if action == 'present':
                packet = scenario.packet(action, context_id=context, candidate_id=candidate)
            else:
                scenario.act('present', context_id=context, candidate_id=candidate)
                if action == 'rework':
                    packet = scenario.packet(action, target_stage_id='write', reason='Повтор этапа.',
                                             entry_tree=scenario.baseline)
                elif action == 'propose_restart':
                    packet = scenario.packet(action, proposal_id='proposal-1', contract=deepcopy(scenario.contract),
                        requirements_snapshot=scenario.initial_snapshot, reason='Повтор договора.')
                else:
                    scenario.proposal()
                    if action == 'decide_restart':
                        packet = scenario.packet(action, actor_id='reviewer-B', proposal_id='proposal-1',
                                                 decision='approved', reason='Пересмотр проверен.')
                    else:
                        packet = scenario.packet(action, proposal_id='proposal-1', approval_id=scenario.approve())
    response = scenario.run(packet)
    scenario.replay(packet, response)
    conflict = deepcopy(packet)
    conflict['input']['actor_id'] = 'executor-C'
    scenario.reject(conflict, 'request')


def test_restart_record_fault_rolls_back_actual_mutation_and_can_continue(scenario):
    scenario.presented()
    scenario.proposal()
    approval = scenario.approve()
    packet = scenario.packet('restart', proposal_id='proposal-1', approval_id=approval)
    # Fault infrastructure only. Canonical state/proposal/decision were reached publicly.
    with sqlite3.connect(scenario.database) as db:
        db.execute("CREATE TRIGGER t053_abort_record BEFORE INSERT ON execution_records "
                   "WHEN NEW.task_id='0042' AND NEW.generation=2 BEGIN "
                   "SELECT RAISE(ABORT,'T053 deliberate record write failure'); END")
    scenario.reject(packet, DATA['expected']['fault'])
    with sqlite3.connect(scenario.database) as db:
        db.execute('DROP TRIGGER t053_abort_record')
    response = scenario.run(packet)
    assert response['generation'] == 2
    scenario.replay(packet, response)
    scenario.preserved()


@pytest.mark.parametrize('field,value', [('task_id', '0043'), ('snapshot_revision', 99)])
def test_freeze_matches_actual_published_identity_and_revision(scenario, field, value):
    Scenario(scenario.root / 'control').freeze()
    snapshot = deepcopy(scenario.initial_snapshot)
    snapshot[field] = value
    scenario.reject(scenario.packet('freeze', requirements_snapshot=snapshot),
                    'task' if field == 'task_id' else 'snapshot')
    assert scenario.freeze()['state'] == 'ready'


def test_approving_actor_must_be_distinct_even_with_configured_approver_role(scenario):
    scenario.prepared()
    scenario.proposal()
    config = json.loads(scenario.config.read_text())
    config['lifecycle']['actors']['executor-A'] = 'reviewer'
    scenario.config.write_text(json.dumps(config))
    scenario.reject(scenario.packet('decide_restart', actor_id='executor-A', proposal_id='proposal-1',
                                    decision='approved', reason='Проверка независимости.'), 'distinct')
    assert scenario.approve()


@pytest.mark.parametrize('field,value', [('proposal_id', 'unknown-proposal'), ('approval_id', 'unknown-approval')])
def test_restart_uses_exact_owned_proposal_and_approval(scenario, field, value):
    scenario.prepared()
    scenario.proposal()
    approval = scenario.approve()
    packet = scenario.packet('restart', proposal_id='proposal-1', approval_id=approval)
    packet['input'][field] = value
    scenario.reject(packet, 'proposal' if field == 'proposal_id' else 'approv')
    assert scenario.act('restart', proposal_id='proposal-1', approval_id=approval)['generation'] == 2


def test_refreeze_uses_exact_agreed_snapshot_and_new_contract_preserves_old_methods(scenario):
    scenario.prepared()
    proposed = deepcopy(scenario.contract)
    proposed['methods'] = [{'id': 'replacement-method'}]
    scenario.proposal(contract=proposed)
    approval = scenario.approve()
    scenario.act('restart', proposal_id='proposal-1', approval_id=approval)
    altered = deepcopy(scenario.initial_snapshot)
    altered['statement']['definition_of_done'] = ['Несогласованное ожидание.']
    scenario.reject(scenario.packet('freeze', requirements_snapshot=altered), 'snapshot')
    assert scenario.freeze()['state'] == 'ready'
    records = scenario.records()
    assert any(r['kind'] == 'contract_snapshot' and r['generation'] == 1
               and r['data']['contract']['methods'] == [{'id': 'original-method'}] for r in records)
    assert any(r['kind'] == 'contract_snapshot' and r['generation'] == 2
               and r['data']['contract']['methods'] == [{'id': 'replacement-method'}] for r in records)


def test_restart_isolated_from_another_canonical_execution_in_same_store(scenario):
    from process_checks import create
    created = scenario.requirements.invoke({'operation': 'create_task', 'input': {
        'request_id': 'neighbor', 'goal': DATA['goal'], 'work_choice': {'kind': 'standalone'}}})
    assert created['task_id'] == '0043'
    revision = created['revision']
    statement = deepcopy(scenario.initial_snapshot['statement'])
    statement = {k: statement[k] for k in ('goal', 'task_requirements', 'definition_of_done')}
    scenario.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'neighbor-statement', 'expected_revision': revision, 'operations': [
            {'kind': 'revise_task', 'task_id': '0043', 'expected_statement_revision': 1,
             'statement': statement}]}})
    scenario.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'neighbor-publication', 'expected_revision': revision + 1, 'operations': [
            {'kind': 'publish_task', 'task_id': '0043', 'statement_revision': 2}]}})
    publication = scenario.requirements.invoke({'operation': 'query', 'input': {'queries': [
        {'id': 'snapshot', 'kind': 'snapshot', 'task_id': '0043', 'snapshot_revision': 1}]}})
    assert publication['results'][0]['value']['task_id'] == '0043'
    scenario.neighbor_before = scenario.neighbors()
    definition = deepcopy(DATA['definition'])
    definition.update(goal_type='notes', worktree_required=False)
    code, process = invoke(scenario.config, create(goal='notes', revision=scenario.revision,
        request='neighbor-process', definition=definition))
    assert code == 0 and process['status'] == 'published', process
    scenario.revision = process['revision']
    contract = deepcopy(scenario.contract)
    contract.update(task_id='0043', requirements_snapshot_revision=1, workspace_id=None,
                    admission_id='explicit-admission-0043',
                    process_ref={'goal_type': 'notes', 'revision': process['process_revision']})
    code, setup = invoke(scenario.config, {'schema': 'stride-execution/v1', 'operation': 'apply',
        'input': {'request_id': 'neighbor-setup', 'expected_revision': scenario.revision,
                  'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': contract}]}})
    assert code == 0 and setup['status'] == 'applied', setup
    scenario.revision = setup['revision']
    def neighbor():
        code, result = invoke(scenario.config, {'schema': 'stride-execution/v1', 'operation': 'query',
            'input': {'queries': [{'id': 'other', 'kind': 'execution', 'task_id': '0043'}]}})
        assert code == 0
        return result['results'][0]['value'], scenario.store.read_records('0043')
    before = neighbor()
    scenario.prepared()
    scenario.proposal()
    scenario.act('restart', proposal_id='proposal-1', approval_id=scenario.approve())
    assert neighbor() == before
    scenario.preserved()


def test_agreed_process_revision_is_frozen_without_rewriting_old_process(scenario):
    scenario.prepared()
    code, changed = invoke(scenario.config, {'schema': 'stride-execution/v1', 'operation': 'process',
        'input': {'action': 'edit', 'request_id': 'new-entry', 'expected_revision': scenario.revision,
                  'goal_type': 'writing', 'expected_process_revision': scenario.contract['process_ref']['revision'],
                  'changes': [{'op': 'patch_route', 'set': {'entry': 'review'}}]}})
    assert code == 0 and changed['status'] == 'published', changed
    scenario.revision = changed['revision']
    proposed = deepcopy(scenario.contract)
    proposed['process_ref']['revision'] = changed['process_revision']
    assert changed['process_revision'] != scenario.contract['process_ref']['revision']
    scenario.proposal(contract=proposed)
    scenario.act('restart', proposal_id='proposal-1', approval_id=scenario.approve())
    scenario.freeze()
    entered = scenario.act('enter', actor_id='reviewer-B', entry_tree=scenario.baseline)
    assert entered['current_stage'] == 'review' and entered['iteration'] == 1
    records = scenario.records()
    assert any(r['kind'] == 'process_snapshot' and r['generation'] == 1
               and r['data']['process']['route']['entry'] == 'write' for r in records)
    assert any(r['kind'] == 'process_snapshot' and r['generation'] == 2
               and r['data']['process_ref']['revision'] == changed['process_revision']
               and r['data']['process']['route']['entry'] == 'review' for r in records)
    scenario.preserved()
