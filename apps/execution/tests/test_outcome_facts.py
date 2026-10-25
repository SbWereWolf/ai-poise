#!/usr/bin/env python3
"""Seven finite public outcome groups, with actual independent recipients."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from outcome_checks import (DATA, LOG, ADVANCE_LOG, MissingOutcome, OutcomeSubject,
    Receiver, Delivery, mark, raw, requirements_packet, sequencing_packet, revision,
    current_state, write_json, invoke, bind)
from apps.sequencing.tests.test_execution_facts import Facts


def child(result, *, accepted=True):
    """Transport success alone never establishes a child business success."""
    value = result['result']
    assert value['disposition'] == 'returned', result
    packet = value['packet']
    assert value['exit_code'] == (0 if accepted else 2), result
    assert packet['status'] == ('applied' if accepted else 'rejected'), result
    return packet


def requirements_rows(s, fact):
    with sqlite3.connect(s.requirements.database) as db:
        rows = db.execute('SELECT outcome_id,task_id,snapshot_revision,kind,keys '
            'FROM requirement_outcomes WHERE outcome_id=?', (fact['outcome_id'],)).fetchall()
    assert [(a, b, c, d, json.loads(e)) for a, b, c, d, e in rows] == [(
        fact['outcome_id'], '0042', 3, fact['kind'], fact['satisfied_task_requirements'])]


def delivery(s, root, fact, *, receiver=None):
    receiver = Receiver(root / 'receiver') if receiver is None else receiver
    coordinator = Delivery(root / 'coordinator', s, receiver)
    child(coordinator.invoke('requirements', requirements_packet(s, fact)))
    child(coordinator.invoke('sequencing', sequencing_packet(receiver, fact)))
    requirements_rows(s, fact)
    value = receiver.query('task')
    expected = {'positive': 'completed', 'negative': 'available', 'cancelled': 'cancelled'}[fact['kind']]
    assert value['state'] == expected and value['claim'] is None and value['workspace'] is None, value
    s.read_fact(value=fact)
    return receiver, coordinator


def assert_satisfaction(s, expected, *, outcome_id=None):
    packet = {'operation': 'query', 'input': {'queries': [{'id': 'satisfaction',
        'kind': 'current_satisfaction'}]}}
    expected_revision = revision(s.requirements.database, 'requirements')
    variant = 'positive' if expected else 'negative' if s.expected_keys else 'future'
    if expected:
        assert isinstance(outcome_id, str) and outcome_id
    expected_value = bind(DATA['satisfaction'][variant], {'@OUTCOME': outcome_id})
    response = s.requirements.invoke(packet)
    LOG.append({'requirements_satisfaction': response, 'expected': expected_value})
    # Full independent key/node/child identities, cardinality, applicability and
    # addressed provenance are required even when no current key is applicable.
    assert response == {'status': 'read_only', 'revision': expected_revision,
        'results': [{'id': 'satisfaction', 'value': expected_value}]}, response


def positive(root):
    s = OutcomeSubject(root / 'positive').terminal()
    packet = s.outcome_packet()
    result = s.record(packet)
    fact = result['fact']
    mark('positive_full_fact_and_current_key_filter')
    # Another CLI process reopens the real store on every query.
    s.read_fact(value=fact)
    mark('public_query_and_reopen')
    receiver = Receiver(root / 'positive-delivery/receiver')
    coordinator = Delivery(root / 'positive-delivery/coordinator', s, receiver)
    foreign_before = s.neighbors(), current_state(receiver.database)
    query = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {'queries': [{
        'id': 'read', 'kind': 'outcome', 'task_id': '0042', 'outcome_id': fact['outcome_id']}]}}
    own = coordinator.invoke('execution', query)['result']
    assert own['exit_code'] == 0 and own['packet']['results'][0]['result'] == fact
    assert (s.neighbors(), current_state(receiver.database)) == foreign_before
    mark('foreign_noeffect_before_transfer')
    child(coordinator.invoke('requirements', requirements_packet(s, fact)))
    child(coordinator.invoke('sequencing', sequencing_packet(receiver, fact)))
    requirements_rows(s, fact)
    assert receiver.query('task')['state'] == 'completed'
    assert_satisfaction(s, True, outcome_id=packet['input']['outcome_id'])
    mark('real_orchestrator_to_both_receivers')
    # Also exercise a genuine ready Workspace receiver, after releasing its real
    # claim. The fact writer does not arrange or release that foreign context.
    work = s.workspace
    before = work.task()['workspace']
    work.client.invoke({'schema': 'stride-sequencing/v1', 'operation': 'apply', 'input': {
        'request_id': 'release-for-outcome', 'expected_revision': revision(work.database, 'sequencing'),
        'operations': [{'id': 'release', 'kind': 'release_claim', 'task_id': '0042',
            'claim_id': work.claim_id, 'claimant': work.owner, 'reason': 'Передать результат.'}]}})
    adapter = Receiver.__new__(Receiver)
    adapter.client, adapter.config, adapter.database, adapter.serial = work.client, work.client.config, work.database, 0
    child(Delivery(root / 'ready-delivery', s, adapter).invoke('sequencing', sequencing_packet(adapter, fact)))
    assert work.task()['workspace'] == before and work.task()['state'] == 'completed'
    empty = OutcomeSubject(root / 'future', future=True).terminal()
    empty_fact = empty.record()['fact']
    assert empty_fact['satisfied_task_requirements'] == []
    delivery(empty, root / 'future-delivery', empty_fact)
    assert_satisfaction(empty, False)
    mark('positive_empty_current_keys_real_future_only_publication')


def nonpositive(root):
    for outcome in ('not_satisfied', 'inconclusive'):
        s = OutcomeSubject(root / outcome, variant='check').terminal(outcome)
        fact = s.record(s.outcome_packet(result=False), kind='negative')['fact']
        assert fact['handler_outcome'] == outcome and fact['satisfied_task_requirements'] == []
        receiver, _ = delivery(s, root / (outcome + '-delivery'), fact)
        assert receiver.query('task')['terminal'] is None
        assert_satisfaction(s, False)
        mark(outcome)
    s = OutcomeSubject(root / 'null-result').terminal()
    fact = s.record(s.outcome_packet(result=False))['fact']
    receiver = Receiver(root / 'null-receiver')
    receiver.apply([{'id': 'successor', 'kind': 'register_task', 'task_id': '0043',
        'requirements_snapshot_revision': 3}, {'id': 'sprint', 'kind': 'register_sprint',
        'sprint_id': 'OUTCOME-SPRINT', 'members': ['0042', '0043'],
        'dependencies': [{'predecessor': '0042', 'successor': '0043', 'kind': 'result'}]}])
    delivery(s, root / 'null-delivery', fact, receiver=receiver)
    eligibility = receiver.query('eligibility', '0043')
    assert any(item['reason'] == 'missing_predecessor_result' for item in eligibility['blockers']), eligibility
    mark('positive_null_commit_with_result_dependency')


def cancellation(root):
    for state in ('draft', 'ready', 'active', 'verified_before_final_decision'):
        s = OutcomeSubject(root / state)
        if state == 'verified_before_final_decision':
            s.terminal()
        elif state != 'draft':
            s.freeze()
            if state == 'active':
                s.enter()
        packet = s.outcome_packet(intent='cancel')
        saved = s.record(packet, kind='cancelled')
        fact = saved['fact']
        assert fact['provenance'] == DATA['cancel_provenance'] and fact['source'] is None
        delivery(s, root / (state + '-delivery'), fact)
        assert_satisfaction(s, False)
        mark(state)
        after = s.state()
        code, replay = raw(s.config, packet)
        assert code == 0 and replay == dict(saved, replayed=True)
        assert s.state() == after
        mark('exact_cancel_replay_preserved')
        if state == 'verified_before_final_decision':
            s.reject(s.outcome_packet(), 'decid', 'outcome', 'finish', 'заверш')
            s.read_fact(value=fact)
            mark('fresh_accept_after_cancel_same_decided_work_refused')
        # Existing test-owned valid optional contexts; real receiver/public
        # record operation is the subject, not Workspace creation in this Task.
        # Snapshot2 is also a genuine retained publication of this same Task.
        if state in ('active', 'verified_before_final_decision'):
            status = 'failed' if state == 'active' else 'released'
            context = Facts(root / (state + '-context'), reference='snapshot')
            context.workspace('0042', status)
            before = current_state(context.database)
            adapter = Receiver.__new__(Receiver)
            adapter.client, adapter.config = context.client, context.client.config
            adapter.database, adapter.serial = context.database, 0
            coordinator = Delivery(root / (state + '-context-coordinator'), s, adapter)
            child(coordinator.invoke('sequencing', sequencing_packet(adapter, fact)))
            actual = current_state(context.database)
            for table in ('workspaces', 'workspace_roots', 'workspace_bindings'):
                assert actual['rows'][table] == before['rows'][table]
            assert adapter.query('task')['state'] == 'cancelled'
            mark('cancel_preserves_' + status + '_context')


def replay_history(root):
    s = OutcomeSubject(root / 'history', variant='review_terminal').terminal()
    packet = s.outcome_packet()
    saved = s.record(packet)
    after = s.state()
    config = json.loads(s.config.read_text())
    historical_config = deepcopy(config)
    del historical_config['outcomes']
    write_json(s.config, historical_config)
    s.read_fact(value=saved['fact'])
    code, result = raw(s.config, packet)
    assert code == 0 and result == dict(saved, replayed=True) and s.state() == after
    write_json(s.config, config)
    mark('exact_request_replay')
    equal = deepcopy(packet)
    equal['input'].update(request_id=s.identity('equal'), expected_revision=s.revision)
    code, result = raw(s.config, equal)
    assert code == 0 and result == dict(saved, changed=False, revision=s.revision + 1), result
    s.revision = result['revision']
    conflict = deepcopy(packet)
    conflict['input']['reason'] = 'Другая причина.'
    s.reject(conflict, 'request', 'запрос')
    conflict = deepcopy(equal)
    conflict['input'].update(request_id=s.identity('conflict'), expected_revision=s.revision,
        reason='Другая причина.')
    s.reject(conflict, 'outcome', 'identity', 'идентич', 'содерж')
    mark('equal_and_conflicting_fact_identity')
    s.reject(s.outcome_packet(intent='cancel'), 'decid', 'outcome', 'заверш')
    mark('fresh_cancel_after_accept_same_work_refused')
    s.reject(s.outcome_packet(), 'decid', 'outcome', 'заверш')
    mark('fresh_accept_id_without_new_work_refused')
    # A real second canonical ID, admitted into the same own provider.
    created = s.requirements.invoke({'operation': 'create_task', 'input': {
        'request_id': 'second-task',
        'goal': 'Вторая самостоятельная задача.', 'work_choice': {'kind': 'standalone'}}})
    assert created['task_id'] == '0043'
    statement = deepcopy(DATA['statement'])
    statement['goal'] = 'Вторая самостоятельная задача.'
    revised = s.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'second-statement', 'expected_revision': created['revision'],
        'operations': [{'kind': 'revise_task', 'task_id': '0043',
            'expected_statement_revision': 1, 'statement': statement}]}})
    s.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'second-publication', 'expected_revision': revised['revision'],
        'operations': [{'kind': 'publish_task', 'task_id': '0043', 'statement_revision': 2}]}})
    snapshot = s.requirements.invoke({'operation': 'query', 'input': {'queries': [{
        'id': 'second', 'kind': 'snapshot', 'task_id': '0043', 'snapshot_revision': 1}]}})
    assert snapshot['results'][0]['value']['statement']['goal'] == statement['goal']
    second_contract = deepcopy(s.contract)
    second_contract['task_id'] = '0043'
    second_contract['requirements_snapshot_revision'] = 1
    second_contract['admission_id'] = 'admission-second-task'
    code, setup = invoke(s.config, {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
        'request_id': 'second-execution', 'expected_revision': s.revision,
        'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': second_contract}]}})
    assert code == 0, setup
    s.revision = setup['revision']
    cross = s.outcome_packet(intent='cancel', identifier=saved['fact']['outcome_id'])
    cross['input'].update(task_id='0043', expected_generation=1,
        expected_contract_revision=setup['results'][0]['contract_revision'])
    s.reject(cross, 'outcome', 'identity', 'task', 'идентич')
    mark('cross_task_identity_conflict')
    missing = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {'queries': [{
        'id': 'missing', 'kind': 'outcome', 'task_id': '0042', 'outcome_id': 'missing-outcome'}]}}
    s.reject(missing, 'outcome', 'unknown', 'missing', 'исход')
    old = saved['fact']
    s.restart()
    s.read_fact(value=old)
    proofs = s.current(stages=('write', 'review'))
    replayed = s.ff(proofs)
    assert replayed['status'] == 'route_end' and replayed['traversed'][-1]['source_actor_ids'] == ['reviewer-B']
    newer = s.record()['fact']
    assert newer['generation'] == 2 and newer['provenance']['source_generation'] == 1
    assert newer['provenance']['source_actor_ids'] == ['reviewer-B']
    assert newer['provenance']['current_evidence_id'] != newer['provenance']['evidence_id']
    s.read_fact(value=old)
    mark('history_after_restart')
    mark('cross_role_replayed_terminal')
    mark('new_outcome_after_approved_restart')
    # Read/replay an old saved request while genuinely new work is active.
    # A separate subject avoids reusing proposal-1 in the accepted old helper.
    rework = OutcomeSubject(root / 'rework', variant='check').terminal('not_satisfied')
    negative_packet = rework.outcome_packet(result=False)
    negative = rework.record(negative_packet, kind='negative')['fact']
    rework.act('rework', target_stage_id='finish', reason='Повторить предметную проверку.',
        entry_tree=rework.baseline)
    rework.payload.write_bytes(b'OK\n')
    rework.verdict = 'proved'
    active = rework.state()
    code, old_replay = raw(rework.config, negative_packet)
    assert code == 0 and old_replay['replayed'] and old_replay['fact'] == negative
    assert rework.state() == active
    rework.read_fact(value=negative)
    rework.perform('finish', outcome='satisfied', tag='actual-rework')
    new = rework.record()['fact']
    assert new['generation'] == negative['generation'] == 1
    assert new['iteration'] == 2 and new['outcome_id'] != negative['outcome_id']
    rework.read_fact(value=negative)
    mark('negative_then_same_generation_real_rework_then_positive')


def admission(root):
    s = OutcomeSubject(root / 'policy', capability=False).terminal()
    # Ordinary old read remains valid; do not classify an unknown operation as a
    # successful missing-configuration rejection in the pre-implementation RED.
    assert s.sync()['state'] == 'verified'
    s.reject(s.outcome_packet(), 'config', 'outcomes', 'настро')
    mark('valid_old_config_without_outcome_capability')
    config = json.loads(s.config.read_text())
    config['outcomes'] = {'roles': ['writer']}
    write_json(s.config, config)
    malformed = [('outcomes_not_an_object', [], ('outcomes',)),
        ('roles_not_an_array', {'roles': 'writer'}, ('roles',)),
        ('roles_empty', {'roles': []}, ('roles',)),
        ('roles_duplicate', {'roles': ['writer', 'writer']}, ('roles', 'duplicate')),
        ('role_not_in_actual_lifecycle_catalogue', {'roles': ['absent']}, ('role', 'actor')),
        ('unknown_outcomes_setting_field', {'roles': ['writer'], 'extra': True}, ('extra', 'field'))]
    for name, setting, tokens in malformed:
        bad = deepcopy(config)
        bad['outcomes'] = setting
        before = s.state()
        packet = s.outcome_packet()
        write_json(s.config, bad)
        code, response = raw(s.config, packet)
        assert code == 2 and response['status'] == 'rejected', response
        assert any(t in response['reason'].lower() for t in tokens), response
        write_json(s.config, config)
        assert s.state() == before
        mark(name)
    changes = [('missing_required_new_input_field', 'outcome_id', None, ('outcome_id',)),
        ('unexpected_new_input_field', 'satisfied_task_requirements', [], ('field', 'unknown', 'unexpected')),
        ('stale_own_expected_revision', 'expected_revision', s.previous_revision, ('revision',)),
        ('denied_actor', 'actor_id', 'reviewer-B', ('role', 'actor')),
        ('unknown_actor', 'actor_id', 'missing-actor', ('actor',)),
        ('stale_generation', 'expected_generation', s.generation + 1, ('generation',)),
        ('stale_contract', 'expected_contract_revision', 'not-current', ('contract',)),
        ('ambiguous_intent', 'intent', 'undecided', ('intent',))]
    for name, key, value, tokens in changes:
        packet = s.outcome_packet()
        if value is None:
            del packet['input'][key]
        else:
            packet['input'][key] = value
        s.reject(packet, *tokens)
        mark(name)
    for field, value in [('reason', ''), ('result_commit', s.source()['commit'])]:
        packet = s.outcome_packet(intent='cancel')
        packet['input'][field] = value
        s.reject(packet, field, 'cancel', 'отмен')
    mark('ambiguous_intent_and_cancel_result')
    # Change the actual test repository, rather than fabricating a source ID.
    original = s.source()
    s.version_file.write_text(DATA['source_changed'])
    s.git('add', 'source-version.txt')
    s.git('commit', '--only', 'source-version.txt', '-m', 'Change actual outcome source')
    changed = s.source()
    assert changed != original
    s.reject(s.outcome_packet(), 'source', 'tree', 'commit')
    # Deliberate test-fixture restoration of this one file and branch; preserve
    # all other staged/unstaged prerequisite work. No application self-repair.
    s.git('update-ref', 'HEAD', original['commit'], changed['commit'])
    s.git('reset', 'HEAD', '--', 'source-version.txt')
    s.version_file.write_text(DATA['source_initial'])
    assert s.source() == original
    mark('changed_actual_source')
    s.record()
    s = OutcomeSubject(root / 'not-admitted').prepared()
    s.reject(s.outcome_packet(), 'terminal', 'route', 'исход')
    s.perform('write')
    s.reject(s.outcome_packet(), 'terminal', 'route', 'исход')
    mark('continuing_stage')
    s.perform('review')
    frame = s.gate_ref('finish')
    s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
    frame = s.observe(frame, replay=False)
    assert s.sync()['state'] == 'verified'
    s.reject(s.outcome_packet(), 'terminal', 'admit', 'route', 'исход')
    mark('presented_but_not_admitted_terminal')
    s.next(frame)
    s.record()


def atomicity(root):
    # First prove the new public seam is present. Otherwise trigger-generated
    # rejection could falsely look like the intended rollback test.
    probe = OutcomeSubject(root / 'probe').terminal()
    probe.record()
    for boundary in ('records', 'requests'):
        s = OutcomeSubject(root / boundary).terminal()
        packet = s.outcome_packet()
        packet['input']['request_id'] = 'atomic-outcome'
        with sqlite3.connect(s.database) as db:
            db.execute(DATA['faults'][boundary])
        before = s.state()
        try:
            s.reject(packet, 'T055')
            assert s.state() == before
        finally:
            with sqlite3.connect(s.database) as db:
                db.execute('DROP TRIGGER ' + ('fail_outcome_records' if boundary == 'records' else 'fail_outcome_request'))
        saved = s.record(packet)
        code, replay = raw(s.config, packet)
        assert code == 0 and replay == dict(saved, replayed=True)
        mark('real_' + boundary + '_write_failure')
    mark('drop_only_fault_and_exact_retry')


def partial_transfer(root):
    s = OutcomeSubject(root / 'producer').terminal()
    fact = s.record()['fact']
    receiver = Receiver(root / 'receiver')
    claimed = receiver.apply([{'id': 'claim', 'kind': 'claim_task', 'task_id': '0042',
        'claimant': 'receiver-worker', 'claim_request_id': 'actual-active-claim'}])
    claim = claimed['results'][0]['claim_id']
    coordinator = Delivery(root / 'coordinator', s, receiver)
    child(coordinator.invoke('requirements', requirements_packet(s, fact)))
    req_before = current_state(s.requirements.database)
    own_before = current_state(s.database)
    refused_packet = sequencing_packet(receiver, fact)
    refused = coordinator.invoke('sequencing', refused_packet, request='blocked-delivery')
    refusal = child(refused, accepted=False)
    assert 'claim' in refusal['reason'].lower(), refusal
    assert current_state(s.requirements.database) == req_before and current_state(s.database) == own_before
    assert receiver.query('task')['claim']['claim_id'] == claim
    s.read_fact(value=fact)
    mark('requirements_accepted_seq_claim_refused')
    receiver.apply([{'id': 'release', 'kind': 'release_claim', 'task_id': '0042',
        'claim_id': claim, 'claimant': 'receiver-worker', 'reason': 'Принять подготовленный результат.'}])
    corrected = sequencing_packet(receiver, fact)
    assert corrected['input']['expected_revision'] != refused_packet['input']['expected_revision']
    succeeded = coordinator.invoke('sequencing', corrected, request='corrected-delivery')
    child(succeeded)
    assert receiver.query('task')['state'] == 'completed'
    assert current_state(s.requirements.database) == req_before and current_state(s.database) == own_before
    mark('release_actual_claim_and_retry_same_fact')
    after = current_state(receiver.database)
    replay = coordinator.invoke('sequencing', corrected, request='corrected-delivery')
    assert replay == dict(succeeded, replayed=True) and current_state(receiver.database) == after
    mark('exact_successful_delivery_replay')


GROUPS = [positive, nonpositive, cancellation, replay_history, admission, atomicity, partial_transfer]


def main():
    errors, failures, details = [], [], []
    for name, test in zip(DATA['case_names'], GROUPS, strict=True):
        start, advance_start = len(LOG), len(ADVANCE_LOG)
        with tempfile.TemporaryDirectory(prefix='execution-outcome-') as directory:
            try:
                test(Path(directory))
                details.append({'id': name, 'status': 'passed'})
            except MissingOutcome:
                failures.append({'id': name, 'reason': 'missing_outcome'})
                details.append({'id': name, 'status': 'missing_outcome',
                    'limit': 'Later subchecks were not executed after the missing public operation.'})
            except AssertionError as exc:
                failures.append({'id': name, 'reason': str(exc) or 'assertion failed'})
                details.append({'id': name, 'status': 'failed', 'traceback': traceback.format_exc()})
            except Exception as exc:
                errors.append({'id': name, 'reason': f'{type(exc).__name__}: {exc}'})
                details.append({'id': name, 'status': 'error', 'traceback': traceback.format_exc()})
        details[-1]['observations'] = deepcopy(LOG[start:])
        details[-1]['prerequisites'] = deepcopy(ADVANCE_LOG[advance_start:])
    report = {'errors': errors, 'failures': failures, 'tests_run': len(GROUPS)}
    output = os.environ.get('POISE_RUN_OUTPUT_DIR')
    if output:
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / 'outcome-facts-details.json').write_text(json.dumps(
            {'report': report, 'groups': details}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))
    return 1 if errors or failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
