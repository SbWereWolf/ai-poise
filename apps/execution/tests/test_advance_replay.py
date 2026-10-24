#!/usr/bin/env python3
"""Actual public advancement, with literal independent route/effect assertions."""
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

from advance_checks import (AdvanceSubject, DATA, LOG, MissingPublicAdvance,
                            clear_fault, install_fault)


def mark(name):
    LOG.append({'subcheck': name})


def cursor(s, result, stage, role, prefix, *, status='work_required', iteration=1):
    assert result['status'] == status, result
    assert (result['current_stage'], result['iteration'], result['required_role']) == (stage, iteration, role), result
    assert [item['stage_id'] for item in result['traversed']] == prefix, result
    actual = s.sync()
    assert (actual['current_stage'], actual['iteration'], actual['state']) == (stage, iteration, result['state'])
    assert result['task_id'] == '0042' and result['generation'] == s.generation
    assert result['contract_revision'] == s.contract_revision and result['replayed'] is False
    s.assert_traversals(result)
    return result


def preserved(s, operation):
    records, physical, neighbors = s.records(), s.physical(), s.neighbors()
    result = operation()
    s.preserved_after(records, physical, neighbors)
    return result


def cross_role(root, actor):
    if actor == 'executor-A':
        oracle_sensitivity()
    s = AdvanceSubject(root).historical()
    proofs = s.current()
    result = preserved(s, lambda: s.ff(proofs, actor=actor))
    cursor(s, result, 'finish', 'writer', ['write', 'review'])
    assert result['stop_reason'] == 'missing_source', result
    assert result['traversed'][0]['source_actor_ids'] == ['executor-A']
    assert result['traversed'][1]['source_actor_ids'] == ['reviewer-B']
    assert s.lifecycle()['registered_goal'] == s.initial_snapshot['statement']['goal']


def gap(root, original):
    s = AdvanceSubject(root).historical(review=not original)
    proofs = s.current(stages=('write', 'review') if original else ('write',))
    result = preserved(s, lambda: s.ff(proofs))
    cursor(s, result, 'review', 'reviewer', ['write'])
    assert result['stop_reason'] == ('missing_source' if original else 'missing_current_evidence'), result
    if original:
        # Create the missing historical outcome in the actual current generation.
        s.perform('review', tag='new-origin')
        s.restart()
        control = s.ff(s.current())
    else:
        control = s.ff(s.current(stages=('review', 'finish')))
    cursor(s, control, 'finish', 'writer', ['write', 'review'] if original else ['review'])


def stale_source(root):
    s = AdvanceSubject(root).historical()
    proofs = s.current()
    s.version_file.write_text(DATA['source_changed'])
    s.git('add', 'source-version.txt')
    s.git('commit', '--only', 'source-version.txt', '-m', 'Change actual subject source')
    result = preserved(s, lambda: s.ff(proofs))
    cursor(s, result, 'write', 'writer', [])
    assert result['stop_reason'] == 'stale_source', result
    cursor(s, s.ff(s.current()), 'finish', 'writer', ['write', 'review'])


def changed_contract(root, change):
    s = AdvanceSubject(root)
    s.prepared()
    s.perform('write')
    s.perform('review')
    contract, definition = deepcopy(s.contract), None
    if change == 'method':
        contract['methods'][0]['stdout_contains'].append('O')
    else:
        definition = deepcopy(s.definition)
        definition['content_contract']['requirements'][0]['states'] = ['populated', 'template']
    s.restart(contract=contract, definition=definition)
    result = preserved(s, lambda: s.ff(s.current()))
    cursor(s, result, 'write', 'writer', [])
    assert result['stop_reason'] == 'changed_contract', result
    frame = s.gate_ref('write', tag='new-work')
    s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
    frame = s.observe(frame, replay=False)
    wrong = s.packet_advance('next', actor='reviewer-B', proof={
        **{k: frame[k] for k in ['context_id', 'candidate_id', 'evidence_id']}, 'outcome': 'complete'})
    s.denied(wrong, 'role')
    result = s.next(frame)
    assert result['current_stage'] == 'review' and result['required_role'] == 'reviewer'


def failed_current(root, missing):
    s = AdvanceSubject(root, extra_guard=missing).historical()
    if not missing:
        s.payload.write_bytes(b'FAILED\n')
    frame = s.gate_ref('write')
    proofs = [s.observe(frame, omit=('extra',) if missing else ())]
    result = preserved(s, lambda: s.ff(proofs))
    cursor(s, result, 'write', 'writer', [])
    assert result['stop_reason'] == ('missing_observation' if missing else 'failed_observation'), result
    s.payload.write_bytes(b'OK\n')
    cursor(s, s.ff(s.current()), 'finish', 'writer', ['write', 'review'])


def atomic_replay(root):
    s = AdvanceSubject(root).historical()
    packet = s.ff_packet(s.current())
    install_fault(s.database)
    before = s.state()
    try:
        code, failure = s.advance(packet)
        assert code == 2 and DATA['fault_message'] in failure['reason'], failure
        assert s.state() == before
    finally:
        clear_fault(s.database)
    code, result = s.advance(packet)
    assert code == 0, result
    cursor(s, result, 'finish', 'writer', ['write', 'review'])
    after = s.state()
    code, replay = s.advance(packet)
    assert code == 0 and replay == dict(result, replayed=True), replay
    assert s.state() == after


def rework_lineage(root):
    s = AdvanceSubject(root)
    s.prepared()
    s.perform('write')
    s.perform('review')
    old = deepcopy(s.origin)
    frame = s.gate_ref('finish')
    s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
    s.act('rework', target_stage_id='write', reason='Уточнить результат.', entry_tree=s.baseline)
    s.perform('write', tag='reworked')
    assert s.origin['write']['visit_id'] != old['write']['visit_id']
    s.restart()
    result = preserved(s, lambda: s.ff(s.current()))
    cursor(s, result, 'review', 'reviewer', ['write'])
    assert result['stop_reason'] == 'superseded_source', result
    visits = s.progression()['accepted_visits']
    assert any(v['visit_id'] == old['review']['visit_id'] for v in visits)


def target_review(root):
    s = AdvanceSubject(root).historical()
    result = preserved(s, lambda: s.ff(s.current(), target='review'))
    cursor(s, result, 'review', 'reviewer', ['write'], status='target_reached')
    assert result['stop_reason'] == 'target_reached'


def oracle_sensitivity():
    """Unit-check the actual cursor oracle, never fabricate a product receipt."""
    values = DATA['oracle_control']
    s = object.__new__(AdvanceSubject)
    s.generation, s.contract_revision = 2, 'oracle-contract'
    s.origins_by_visit = {values['origin']['visit_id']: values['origin']}
    s.current_proofs = {'write': values['current']}
    s.sync = lambda: {'current_stage': 'review', 'iteration': 1, 'state': 'active'}
    cursor(s, deepcopy(values['result']), 'review', 'reviewer', ['write'], status='target_reached')
    LOG.append({'oracle_control': 'correct independent references accepted'})
    for key, wrong in [('gate_id', 'wrong-old-gate'), ('evidence_id', 'wrong-old-evidence'),
                       ('current_evidence_id', 'wrong-current-evidence'), ('iteration', 999)]:
        changed = deepcopy(values['result'])
        changed['traversed'][0][key] = wrong
        try:
            cursor(s, changed, 'review', 'reviewer', ['write'], status='target_reached')
        except AssertionError:
            LOG.append({'oracle_control': key, 'wrong_value_rejected': True})
        else:
            raise AssertionError('Cursor oracle accepted wrong ' + key)


def foreign_proof(s):
    """A real second Task/generation in the same store, created by public APIs."""
    from process_checks import invoke
    created = s.requirements.invoke({'operation': 'create_task', 'input': {
        'request_id': 'foreign-task', 'goal': s.initial_snapshot['statement']['goal'],
        'work_choice': {'kind': 'standalone'}}})
    assert created['task_id'] == '0043'
    revised = s.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'foreign-statement', 'expected_revision': created['revision'], 'operations': [{
            'kind': 'revise_task', 'task_id': '0043', 'expected_statement_revision': 1,
            'statement': {k: s.initial_snapshot['statement'][k]
                          for k in ['goal', 'task_requirements', 'definition_of_done']}}]}})
    s.requirements.invoke({'operation': 'apply', 'input': {
        'request_id': 'foreign-publish', 'expected_revision': revised['revision'], 'operations': [{
            'kind': 'publish_task', 'task_id': '0043', 'statement_revision': 2}]}})
    snapshot = s.requirements.invoke({'operation': 'query', 'input': {'queries': [{
        'id': 'foreign', 'kind': 'snapshot', 'task_id': '0043', 'snapshot_revision': 1}]}})['results'][0]['value']
    contract = dict(deepcopy(s.contract), task_id='0043', requirements_snapshot_revision=1)
    code, setup = invoke(s.config, {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
        'request_id': 'foreign-setup', 'expected_revision': s.sync_revision(),
        'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': contract}]}})
    assert code == 0, setup
    saved = (s.task_id, s.generation, s.contract_revision)
    s.task_id = '0043'
    def action(name, actor='executor-A', **fields):
        packet = s.packet_advance(name, actor=actor)
        packet['operation'] = 'lifecycle'
        for key in ['source', 'entry_tree']:
            packet['input'].pop(key)
        packet['input'].update(fields)
        code, result = invoke(s.config, packet)
        assert code == 0, result
        s.sync()
        return result
    try:
        action('freeze', requirements_snapshot=snapshot)
        action('enter', entry_tree=s.baseline)
        action('propose_restart', actor='executor-C', proposal_id='foreign-proposal',
               contract=contract, requirements_snapshot=snapshot, reason='Подготовить отдельную задачу.')
        approval = action('decide_restart', actor='reviewer-D', proposal_id='foreign-proposal',
                          decision='approved', reason='Проверены условия отдельной задачи.')
        action('restart', proposal_id='foreign-proposal', approval_id=approval['result']['approval_id'])
        action('freeze', requirements_snapshot=snapshot)
        action('enter', entry_tree=s.baseline)
        proof = s.observe(s.gate_ref('write'))
        assert s.generation == 2
    finally:
        s.task_id, s.generation, s.contract_revision = saved
        s.sync()
    return proof


def proof_binding_group(root):
    for name in ['task', 'stage', 'generation', 'context']:
        mark('binding/' + name)
        s = AdvanceSubject(root / name).historical()
        good = s.current(stages=('write',))[0]
        if name == 'task':
            bad = foreign_proof(s)
        elif name == 'stage':
            bad = s.observe(s.gate_ref('review'))
        elif name == 'generation':
            bad = s.first_evidence
        else:
            bad = s.observe(s.gate_ref('write', tag='other'))
        mismatched = dict(good, evidence_id=bad['evidence_id'])
        s.denied(s.ff_packet([mismatched]), name)
        # Only the mismatched actual proof reference changes for the control.
        result = s.ff([good], target='review')
        cursor(s, result, 'review', 'reviewer', ['write'], status='target_reached')
    for name in ['missing_review', 'stale_review', 'negative_review']:
        mark('evidence/' + name)
        r = AdvanceSubject(root / name)
        r.prepared()
        r.perform('write')
        old_revision = r.latest_argument()
        if name == 'stale_review':
            frame = r.gate_ref('review')
            r.act('present', actor_id='reviewer-B', context_id=frame['context_id'],
                  candidate_id=frame['candidate_id'])
            r.act('rework', actor_id='reviewer-B', target_stage_id='write',
                  reason='Уточнить обоснование.', entry_tree=r.baseline)
            r.perform('write', tag='revised')
            assert r.latest_argument() != old_revision
        frame = r.gate_ref('review', tag='review-decision')
        r.act('present', actor_id='reviewer-B', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        proof = r.observe(frame, replay=False, missing_review=name == 'missing_review',
                          review_revision=old_revision if name == 'stale_review' else None,
                          decision='rejected' if name == 'negative_review' else 'accepted')
        packet = r.packet_advance('next', actor='reviewer-B', proof={
            **{k: proof[k] for k in ['context_id', 'candidate_id', 'evidence_id']}, 'outcome': 'clear'})
        if name == 'missing_review':
            code, stopped = r.advance(packet)
            assert code == 0
            assert stopped['status'] == 'work_required' and stopped['stop_reason'] == 'missing_review'
            assert stopped['traversed'] == [] and r.execution()['current_stage'] == 'review'
        else:
            r.denied(packet, 'revision' if name == 'stale_review' else 'decision')
        if name == 'negative_review':
            result = r.next(proof, outcome='changes_requested')
            assert result['current_stage'] == 'write' and result['iteration'] == 2
            r.perform('write', tag='corrected')
            frame = r.gate_ref('review', tag='accepted-decision')
            r.act('present', actor_id='reviewer-B', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        corrected = r.observe(frame, replay=False)
        assert r.next(corrected)['current_stage'] == 'finish'
    for name in ['incomplete', 'uninterpretable', 'contradictory']:
        mark('evidence/' + name)
        c = AdvanceSubject(root / name).historical()
        c.payload.write_bytes(b'OK\nextra\n' if name == 'incomplete' else
                              b'OK\xff\n' if name == 'uninterpretable' else b'FAILED\n')
        frame = c.gate_ref('write')
        proof = c.observe(frame, capture=3 if name == 'incomplete' else None,
                          contradiction=name == 'contradictory')
        if name == 'contradictory':
            c.denied(c.ff_packet([proof]), 'contradict')
            proof = c.observe(frame)
        result = c.ff([proof])
        cursor(c, result, 'write', 'writer', [])
        assert result['stop_reason'] == {'incomplete': 'incomplete_capture',
            'uninterpretable': 'uninterpretable_observation', 'contradictory': 'failed_observation'}[name], result
        c.payload.write_bytes(b'OK\n')
        cursor(c, c.ff(c.current()), 'finish', 'writer', ['write', 'review'])


def guard_group(root):
    s = AdvanceSubject(root).historical()
    previous_contract = s.contract_revision
    changed = deepcopy(s.contract)
    changed['methods'][0]['stdout_contains'].append('O')
    s.restart(contract=changed)
    assert s.contract_revision != previous_contract
    proofs = s.current()
    saved = s.ff_packet(proofs, target='write')
    code, _ = s.advance(saved)
    assert code == 0
    variants = [
        ('revision', lambda p: p.update(expected_revision=p['expected_revision'] - 1), 'revision'),
        ('generation', lambda p: p.update(expected_generation=2), 'generation'),
        ('contract', lambda p: p.update(expected_contract_revision=previous_contract), 'contract'),
        ('actor', lambda p: p.update(actor_id='unconfigured-actor'), 'actor'),
        ('identity', lambda p: p.update(request_id=saved['input']['request_id'], actor_id='reviewer-B'), 'request'),
        ('action', lambda p: p.update(action=[]), 'action'),
        ('proofs', lambda p: p.update(proofs={}), 'proofs'),
        ('source', lambda p: p.update(source=[]), 'source'),
        ('integer', lambda p: p.update(expected_revision=True), 'integer'),
        ('duplicate', lambda p: p['proofs'].append(deepcopy(p['proofs'][0])), 'duplicate'),
    ]
    for name, alter, reason in variants:
        mark('guards/' + name)
        packet = s.ff_packet(proofs, target='write')
        alter(packet['input'])
        s.denied(packet, reason)
        cursor(s, s.ff(proofs, target='write'), 'write', 'writer', [], status='target_reached')


def route_group(root):
    s = AdvanceSubject(root / 'linear').historical(finish=True)
    proofs = s.current()
    mark('routes/current')
    cursor(s, s.ff(proofs, target='write'), 'write', 'writer', [], status='target_reached')
    mark('routes/unknown')
    s.denied(s.ff_packet(proofs, target='unknown-stage'), 'target')
    cursor(s, s.ff(proofs, target='write'), 'write', 'writer', [], status='target_reached')
    mark('routes/unreachable')
    cursor(s, s.ff(proofs, target='finish'), 'finish', 'writer', ['write', 'review'], status='target_reached')
    s.denied(s.ff_packet(proofs, target='write'), 'reachable')
    cursor(s, s.ff(proofs, target='finish'), 'finish', 'writer', [], status='target_reached')
    mark('routes/end')
    result = s.ff(proofs)
    cursor(s, result, 'finish', None, ['finish'], status='route_end')
    assert result['state'] == 'verified' and result['stop_reason'] == 'route_end'
    assert s.execution()['state'] != 'completed'
    mark('routes/corrective-cycle')
    c = AdvanceSubject(root / 'cycle')
    c.prepared()
    c.perform('write')
    c.perform('review', outcome='changes_requested', decision='rejected')
    c.restart()
    result = preserved(c, lambda: c.ff(c.current()))
    cursor(c, result, 'write', 'writer', ['write', 'review'], iteration=2)
    assert result['stop_reason'] == 'visited_source', result


def fault_control(root, record_first):
    root.mkdir(parents=True)
    for atomic in [False, True]:
        db = sqlite3.connect(root / ('atomic.sqlite' if atomic else 'unprotected.sqlite'), isolation_level=None)
        for sql in DATA['fault_tables']:
            db.execute(sql)
        db.execute("INSERT INTO executions VALUES('0042',2,'write')")
        for sql in DATA['fault_triggers']:
            db.execute(sql)
        effects = ["INSERT INTO execution_records VALUES('0042','advance_traversal',2)",
                   "UPDATE executions SET current_stage='review' WHERE task_id='0042'"]
        if not record_first:
            effects.reverse()
        if atomic:
            db.execute('BEGIN')
        try:
            db.execute(effects[0])
            db.execute(effects[1])
        except sqlite3.IntegrityError as exc:
            assert str(exc) == DATA['fault_message']
            if atomic:
                db.rollback()
        else:
            raise AssertionError('The second substantive effect was not rejected')
        stage = db.execute('SELECT current_stage FROM executions').fetchone()[0]
        count = db.execute('SELECT COUNT(*) FROM execution_records').fetchone()[0]
        assert (stage, count) == (('write', 0) if atomic else
                                  ('write', 1) if record_first else ('review', 0))
        LOG.append({'fault_control': {'record_first': record_first, 'atomic': atomic,
                                     'stage': stage, 'records': count}})
        db.close()


class RegressionMismatch(AssertionError):
    def __init__(self, finding, actual):
        self.finding = finding
        super().__init__(repr(actual))


def regression(condition, finding, actual):
    if not condition:
        raise RegressionMismatch(finding, actual)


def subject_outcomes(root):
    from advance_checks import CorrectionSubject
    for case in DATA['corrections']['subject_cases']:
        mark('subject/' + case['name'])
        s = CorrectionSubject(root / case['name'], subject_check=True)
        s.contract['evidence_plan']['write']['subject_methods']['guard']['exit_codes'] = case['rule_codes']
        # Publish the explicit variation through the existing setup before entry.
        if case['rule_codes'] != [0, 7]:
            s.prepared()
            changed = deepcopy(s.contract)
            s.restart(contract=changed)
        else:
            s.prepared()
        s.payload.write_text(case['payload'])
        frame = s.gate_ref('write')
        s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        proof = s.subject_observation(frame, guard=case['guard'], capture=case['capture'])
        result = s.next(proof, outcome=case['outcome'])
        actual = {'stage': result['current_stage'], 'status': result['status'],
                  'prefix': [item['stage_id'] for item in result['traversed']]}
        expected = {key: case[key] for key in actual}
        regression(actual == expected, 'T054-CODE-F01', (case['name'], actual, expected))
        if result['traversed']:
            regression(result['traversed'][0]['outcome'] == case['outcome'], 'T054-CODE-F01', result)
        else:
            regression(result['stop_reason'] in ['failed_observation', 'incomplete_capture',
                       'missing_argument', 'uninterpretable_observation'], 'T054-CODE-F01', result)


def current_rework_lineage(root):
    from advance_checks import CorrectionSubject
    s = CorrectionSubject(root).historical()
    s.ff(s.current())
    old_visits = deepcopy(s.progression()['accepted_visits'])
    frame = s.gate_ref('finish', tag='actual-current-rework')
    s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
    s.act('rework', target_stage_id='write', reason='Выполнить новую работу автора.', entry_tree=s.baseline)
    entry = s.sync()
    assert (entry['generation'], entry['current_stage'], entry['iteration']) == (2, 'write', 2)
    proofs = [s.observe(s.gate_ref(stage, tag='fresh-rework', iteration=2))
              for stage in ['write', 'review', 'finish']]
    result = s.ff(proofs)
    regression((result['current_stage'], result['iteration'], result['traversed'], result['stop_reason'])
               == ('write', 2, [], 'superseded_source'), 'T054-CODE-F02', result)
    regression(s.progression()['accepted_visits'] == old_visits, 'T054-CODE-F02', s.progression())
    mark('rework/newly-performed-work')
    write = s.perform('write', tag='actual-new-write')
    review = s.perform('review', tag='actual-new-review')
    regression((write['current_stage'], review['current_stage']) == ('review', 'finish'),
               'T054-CODE-F02', (write, review))
    new_ids = [s.origin[stage]['visit_id'] for stage in ['write', 'review']]
    s.restart()
    replay = s.ff(s.current())
    regression(replay['current_stage'] == 'finish'
               and [item['visit_id'] for item in replay['traversed']] == new_ids,
               'T054-CODE-F02', replay)


def external_record_shapes(root):
    from advance_checks import CorrectionSubject
    for name, reason_field in zip(DATA['corrections']['malformed_cases'],
                                  DATA['corrections']['malformed_reason_fields'], strict=True):
        mark('external-shape/' + name)
        s = CorrectionSubject(root / name).prepared()
        frame = s.gate_ref('write')
        s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        genuine = s.observe(frame, replay=False)
        records = deepcopy(next(item['public_records'] for item in reversed(LOG)
                                if 'public_records' in item))
        attempt, evidence = records
        attempt['id'] = s.identity('malformed-attempt')
        evidence['id'] = s.identity('malformed-evidence')
        evidence['data']['attempt_id'] = attempt['id']
        if name == 'missing_attempt_id':
            evidence['data'].pop('attempt_id')
        elif name == 'missing_attempt_context':
            attempt['data'].pop('context_id')
        elif name == 'nonstring_attempt_id':
            evidence['data']['attempt_id'] = ['not-an-id']
        elif name == 'bad_work_arguments':
            evidence['data']['evidence_work']['arguments'] = {'not': 'a-list'}
        elif name == 'nonstring_receipt_method':
            attempt['data']['receipts'][0]['method'] = ['not-an-id']
        elif name == 'invalid_source_scalar':
            attempt['data']['source']['tree'] = ['not-a-tree']
        elif name == 'invalid_exit_scalar':
            attempt['data']['receipts'][0]['actual_exit_code'] = 'zero'
        if name == 'nonstring_attempt_id':
            # This identity belongs to the existing public writer. Its lawful
            # refusal must roll back the complete attempted batch.
            from apps.execution.errors import ExecutionError
            before = s.state()
            try:
                s.write_proof(frame, records)
            except ExecutionError as exc:
                regression('attempt_id' in str(exc), 'T054-CODE-F03', str(exc))
                LOG.append({'producer_refusal': name, 'reason': str(exc)})
            else:
                raise RegressionMismatch('T054-CODE-F03', 'Writer accepted a non-string attempt_id')
            regression(s.state() == before, 'T054-CODE-F03', name + '/writer changed state')
            control = s.next(genuine)
            regression(control['status'] == 'advanced' and control['current_stage'] == 'review',
                       'T054-CODE-F03', (name, control))
            continue
        bad = s.write_proof(frame, records)
        packet = s.packet_advance('next', proof={**{key: bad[key] for key in
            ['context_id', 'candidate_id', 'evidence_id']}, 'outcome': 'complete'})
        before = s.state()
        code, result, stderr = s.raw_advance(packet)
        # A real product CLI traceback is a regression; imports/preparation above
        # remain ordinary errors and cannot manufacture the expected RED.
        regression(code == 2 and isinstance(result, dict) and result.get('status') == 'rejected'
                   and isinstance(result.get('reason'), str) and bool(result['reason'].strip())
                   and reason_field in result['reason'].lower()
                   and stderr == '', 'T054-CODE-F03', (name, code, result, stderr))
        regression(s.state() == before, 'T054-CODE-F03', name + '/state changed')
        control = s.next(genuine)
        regression(control['status'] == 'advanced' and control['current_stage'] == 'review',
                   'T054-CODE-F03', (name, control))


def restored_entry_control(s, action):
    s.handoff()
    if action == 'next':
        s.act('rework', target_stage_id='write',
              reason='Подготовить результат после восстановления входного материала.', entry_tree=s.baseline)
        active = s.sync()
        assert (active['state'], active['current_stage'], active['iteration']) == ('active', 'write', 2), active
    frame = s.gate_ref('write', tag='restored-entry')
    if action == 'next':
        s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        return s.next(s.observe(frame, replay=False))
    return s.ff([s.observe(frame)], target='review' if action == 'target' else None)


def entry_admission(root):
    from advance_checks import CorrectionSubject
    for action in ['next', 'fast_forward', 'target']:
        mark('entry/' + action)
        s = CorrectionSubject(root / action, entry=True)
        s.handoff()
        s.prepared()
        if action != 'next':
            s.perform('write', tag='original-write')
            s.perform('review', tag='original-review')
            s.restart()
        s.handoff_file.unlink()
        # Current write proof is genuinely accepted while the following stage's
        # preexisting artifact is absent. It is not its own exit condition.
        frame = s.gate_ref('write', tag='missing-entry')
        if action == 'next':
            s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
            proof = s.observe(frame, replay=False)
            before = s.lifecycle()
            progress_before = s.progression()
            result = s.next(proof)
        else:
            proof = s.observe(frame)
            before = s.lifecycle()
            progress_before = s.progression()
            result = s.ff([proof], target='review' if action == 'target' else None)
        regression((result['status'], result['current_stage'], result['iteration'],
                    result['traversed'], result['stop_reason'])
                   == ('work_required', 'write', 1, [], 'entry_blocked'), 'T054-CODE-F04', result)
        regression(s.lifecycle() == before, 'T054-CODE-F04', (before, s.lifecycle()))
        progress_after = s.progression()
        regression(all(progress_after[key] == progress_before[key]
                       for key in ['accepted_visits', 'traversals']), 'T054-CODE-F04', progress_after)
        control = restored_entry_control(s, action)
        regression(control['current_stage'] == 'review'
                   and [item['stage_id'] for item in control['traversed']] == ['write']
                   and control['status'] == ('target_reached' if action == 'target'
                                             else 'advanced' if action == 'next' else 'work_required'),
                   'T054-CODE-F04', control)


def corrections_main():
    cases = [subject_outcomes, current_rework_lineage, external_record_shapes, entry_admission]
    errors, failures = [], []
    with tempfile.TemporaryDirectory(prefix='execution-advance-corrections-') as temporary:
        for name, finding, case in zip(DATA['corrections']['case_names'],
                                      DATA['corrections']['finding_ids'], cases, strict=True):
            LOG.append({'case': name, 'finding': finding})
            try:
                case(Path(temporary) / name)
            except RegressionMismatch as exc:
                assert exc.finding == finding
                failures.append({'test': name, 'reason': finding})
                LOG.append({'regression_failure': name, 'finding': finding,
                            'traceback': traceback.format_exc()})
            except Exception as exc:
                errors.append({'test': name, 'type': type(exc).__name__})
                LOG.append({'case_error': name, 'traceback': traceback.format_exc()})
            else:
                LOG.append({'case_passed': name})
    output = os.environ.get('POISE_RUN_OUTPUT_DIR')
    if output:
        destination = Path(output) / 'advance-corrections-results.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(LOG, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'errors': errors, 'failures': failures, 'tests_run': len(cases)},
                     ensure_ascii=False, separators=(',', ':')))
    return 1 if errors or failures else 0


def contract_guard_requiredness(root):
    from advance_checks import CorrectionSubject
    for case in DATA['followup']['guard_cases']:
        mark('contract-guard/' + case['name'])
        s = CorrectionSubject(root / case['name']).prepared()
        assert s.contract['evidence_plan']['write']['subject_methods'] == {}
        s.payload.write_text(case['payload'])
        frame = s.gate_ref('write')
        s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        s.observe(frame, replay=False)
        records = deepcopy(next(item['public_records'] for item in reversed(LOG) if 'public_records' in item))
        attempt, evidence = records
        attempt['id'], evidence['id'] = s.identity('contract-attempt'), s.identity('contract-evidence')
        evidence['data']['attempt_id'] = attempt['id']
        # All command/source/pass facts came from the actual child. This external
        # hint does not change the method's contract-owned requiredness.
        attempt['data']['receipts'][0]['guard'] = case['guard']
        proof = s.write_proof(frame, records)
        before, progress = s.lifecycle(), s.progression()
        result = s.next(proof)
        actual = {'status': result['status'], 'stage': result['current_stage'],
                  'prefix': [item['stage_id'] for item in result['traversed']], 'reason': result['stop_reason']}
        regression(actual == {key: case[key] for key in actual}, 'T054-CODE-F05', (case['name'], actual))
        if not case['prefix']:
            regression(s.lifecycle() == before, 'T054-CODE-F05', s.lifecycle())
            after = s.progression()
            regression(all(after[key] == progress[key] for key in ['accepted_visits', 'traversals']),
                       'T054-CODE-F05', after)


def policy_next_result(s, proof):
    # Prepare every association before the real CLI returns an opaque visit ID.
    origin = {'stage_id': 'write', 'iteration': proof['iteration'], 'source_generation': s.generation,
              'source_actor_ids': ['executor-A'], 'gate_id': proof['gate_id'],
              'evidence_id': proof['evidence_id'], 'outcome': 'complete'}
    packet = s.packet_advance('next', proof={**{key: proof[key] for key in
        ['context_id', 'candidate_id', 'evidence_id']}, 'outcome': 'complete'})
    code, result, stderr = s.raw_advance(packet)
    regression(code == 0 and stderr == '' and isinstance(result, dict),
               'T054-CODE-F06', (code, result, stderr))
    regression(result['status'] == 'advanced' and result['current_stage'] == 'review'
               and len(result['traversed']) == 1, 'T054-CODE-F06', result)
    visit_id = result['traversed'][0]['visit_id']
    assert isinstance(visit_id, str) and visit_id
    s.origins_by_visit[visit_id] = dict(origin, visit_id=visit_id)
    s.current_proofs = {'write': proof}
    s.assert_traversals(result)
    s.assert_public_provenance(result)
    cursor(s, result, 'review', 'reviewer', ['write'], status='advanced')
    return result


def file_policy_entry_catalogue(root):
    from advance_checks import PolicyAdvanceSubject
    for name in DATA['followup']['policy_cases']:
        mark('file-policy/' + name)
        s = PolicyAdvanceSubject(root / name, policy=name != 'ordinary_next',
            entry=name == 'policy_missing_after_gate', deny_review=name == 'policy_read_denied')
        s.handoff()
        if name in ['policy_replay', 'policy_target']:
            s.historical()
            result = s.ff(s.current(), target='review' if name == 'policy_target' else None)
            cursor(s, result, 'review' if name == 'policy_target' else 'finish',
                'reviewer' if name == 'policy_target' else 'writer',
                ['write'] if name == 'policy_target' else ['write', 'review'],
                status='target_reached' if name == 'policy_target' else 'work_required')
            regression(result['stop_reason'] == ('target_reached' if name == 'policy_target' else 'missing_source'),
                       'T054-CODE-F06', result)
            continue
        s.prepared()
        frame = s.gate_ref('write')
        s.act('present', context_id=frame['context_id'], candidate_id=frame['candidate_id'])
        proof = s.observe(frame, replay=False)
        if name in ['policy_next', 'ordinary_next']:
            policy_next_result(s, proof)
            continue
        # The missing-file case specifically removes a KNOWN input only after
        # accepted Gate/presentation and full actual proof, before advancement.
        if name == 'policy_missing_after_gate':
            s.handoff_file.unlink()
        before, progress = s.lifecycle(), s.progression()
        result = s.next(proof)
        regression((result['status'], result['current_stage'], result['iteration'],
                    result['traversed'], result['stop_reason'])
                   == ('work_required', 'write', 1, [], 'entry_blocked'), 'T054-CODE-F06', result)
        regression(s.lifecycle() == before, 'T054-CODE-F06', s.lifecycle())
        after = s.progression()
        regression(all(after[key] == progress[key] for key in ['accepted_visits', 'traversals']),
                   'T054-CODE-F06', after)
        s.deny_review = False
        control = restored_entry_control(s, 'next')
        regression(control['status'] == 'advanced' and control['current_stage'] == 'review'
                   and [item['stage_id'] for item in control['traversed']] == ['write']
                   and control['traversed'][0]['iteration'] == 2, 'T054-CODE-F06', control)


def followup_main():
    cases = [contract_guard_requiredness, file_policy_entry_catalogue]
    errors, failures = [], []
    with tempfile.TemporaryDirectory(prefix='execution-advance-followup-') as temporary:
        for name, finding, case in zip(DATA['followup']['case_names'], DATA['followup']['finding_ids'], cases, strict=True):
            LOG.append({'case': name, 'finding': finding})
            try:
                case(Path(temporary) / name)
            except RegressionMismatch as exc:
                assert exc.finding == finding
                failures.append({'test': name, 'reason': finding})
                LOG.append({'regression_failure': name, 'finding': finding, 'traceback': traceback.format_exc()})
            except Exception as exc:
                errors.append({'test': name, 'type': type(exc).__name__})
                LOG.append({'case_error': name, 'traceback': traceback.format_exc()})
            else:
                LOG.append({'case_passed': name})
    output = os.environ.get('POISE_RUN_OUTPUT_DIR')
    if output:
        destination = Path(output) / 'followup-results.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(LOG, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'errors': errors, 'failures': failures, 'tests_run': len(cases)},
                     ensure_ascii=False, separators=(',', ':')))
    return 1 if errors or failures else 0


def main():
    cases = [
        lambda p: cross_role(p, 'executor-A'), lambda p: cross_role(p, 'reviewer-B'),
        lambda p: gap(p, True), lambda p: gap(p, False), stale_source,
        lambda p: changed_contract(p, 'method'), lambda p: changed_contract(p, 'condition'),
        lambda p: failed_current(p, False), lambda p: failed_current(p, True),
        atomic_replay, rework_lineage, target_review, proof_binding_group, guard_group, route_group,
        lambda p: fault_control(p, True), lambda p: fault_control(p, False),
    ]
    errors, failures = [], []
    with tempfile.TemporaryDirectory(prefix='execution-advance-') as temporary:
        for name, case in zip(DATA['case_names'], cases, strict=True):
            LOG.append({'case': name})
            try:
                case(Path(temporary) / name)
            except MissingPublicAdvance:
                failures.append({'test': name, 'reason': 'missing_advance'})
            except Exception as exc:
                errors.append({'test': name, 'type': type(exc).__name__})
                LOG.append({'case_error': name, 'traceback': traceback.format_exc()})
            else:
                LOG.append({'case_passed': name})
    output = os.environ.get('POISE_RUN_OUTPUT_DIR')
    if output:
        destination = Path(output) / 'advance-results.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(LOG, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'errors': errors, 'failures': failures, 'tests_run': len(cases)},
                     ensure_ascii=False, separators=(',', ':')))
    return 1 if errors or failures else 0


if __name__ == '__main__':
    raise SystemExit(followup_main() if sys.argv[1:] == ['--suite', 'followup']
                     else corrections_main() if sys.argv[1:] == ['--suite', 'corrections'] else main())
