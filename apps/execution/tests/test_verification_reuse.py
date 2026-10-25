#!/usr/bin/env python3
"""Finite preservation checks for standalone Execution verification and reuse."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from verification_checks import (DATA, LOG, MissingVerification, VerificationSubject,
    assert_attempt, assert_receipt, raw, rows, current_revision, write_json)


def mark(name):
    LOG.append({'subcheck': name})


def subject(root, **options):
    return VerificationSubject(root, **options).probe()


def replay(s, packet, expected):
    before, count = rows(s.database), s.count()
    code, value = raw(s.config, packet)
    assert code == 0 and value == dict(expected, replayed=True), value
    assert rows(s.database) == before and s.count() == count
    return value


def budget(s, used, pending=None):
    state = s.sync()
    expected = {'task_id': s.task_id, 'generation': s.generation,
        'stage_id': state['current_stage'], 'iteration': state['iteration'],
        'used': used, 'maximum': s.maximum, 'pending_attempt_id': pending}
    assert s.lookup('verification') == expected
    return expected


def complete(s, *, used=1, mode='good', verdict='proved'):
    packet, attempt = s.begin(used=used)
    attempt = s.run_all(attempt, mode=mode)
    finish, result = s.finish(attempt, verdict=verdict)
    assert_attempt(result, subject=s, identifier=packet['input']['attempt_id'],
        frame=s.frame, schedule=packet['input']['method_ids'], used=used,
        maximum=s.maximum, state='finished')
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == result
    return packet, finish, result


def verification(root):
    s = subject(root / 'ordered', extra=True)
    initial, before, count = s.sync(), s.source(), s.count()
    packet, attempt = s.begin()
    assert s.count() == count
    budget(s, 1, attempt['attempt_id'])
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == attempt
    mark('reserved_shape')
    attempt = s.run_all(attempt)
    assert s.count('guard') == 1 and s.count('guard-extra') == 1
    assert [r['method_id'] for r in attempt['runs']] == ['guard', 'guard-extra']
    mark('ordered_real_receipts')
    _, final = s.finish(attempt)
    assert_attempt(final, subject=s, identifier=packet['input']['attempt_id'],
        frame=s.frame, schedule=['guard', 'guard-extra'], used=1, maximum=8, state='finished')
    assert s.sync() == initial and s.source() == before
    budget(s, 1)
    mark('finished_evidence')
    for mode in ('bad', 'raw', 'missing'):
        case = subject(root / mode)
        case.mode(mode)
        _, _, finished = complete(case, mode=mode,
                                  verdict='disproved' if mode == 'bad' else 'inconclusive')
        assert case.count() == 1
        assert finished['runs'][0]['receipt']['passed'] is False
        assert case.sync()['current_stage'] == 'write'
        if mode == 'bad':
            assert finished['runs'][0]['receipt']['actual_exit_code'] == 7
            mark('actual_failure')
    negative = subject(root / 'subject-negative', variant='check')
    negative.finish_route()
    negative.mode('bad')
    negative.rework_current()
    negative.act('present', context_id=negative.frame['context_id'],
                 candidate_id=negative.frame['candidate_id'])
    _, attempt = negative.begin()
    attempt = negative.run_all(attempt, mode='bad', guard=False)
    _, finished = negative.finish(attempt, verdict='disproved')
    proof = dict(negative.frame, evidence_id=finished['evidence_id'])
    negative.observed_frames[(negative.task_id, negative.generation, 'finish', proof['context_id'],
        proof['candidate_id'], proof['evidence_id'])] = deepcopy(proof)
    negative.next(proof, outcome='not_satisfied')
    fact = negative.record(negative.outcome_packet(result=False), kind='negative')['fact']
    assert fact['handler_outcome'] == 'not_satisfied' and fact['satisfied_task_requirements'] == []
    mark('lossless_bytes_and_outputs')
    # A genuinely empty schedule belongs to the current review stage.
    empty = subject(root / 'empty')
    empty.perform('write')
    empty.frame = empty.gate_ref('review', tag='empty-schedule')
    old_count = empty.count()
    packet, attempt = empty.begin(actor='reviewer-B')
    assert attempt['method_ids'] == [] and attempt['runs'] == []
    _, finished = empty.finish(attempt, actor='reviewer-B')
    assert finished['state'] == 'finished' and finished['runs'] == []
    assert empty.count() == old_count
    budget(empty, 1)
    mark('empty_declared_schedule')


def recovery(root):
    s = subject(root / 'tail', extra=True)
    packet, attempt = s.begin()
    reserved = {'schema': 'stride-execution/v1', 'status': 'attempt_reserved',
        'revision': current_revision(s.database), 'replayed': False,
        'changed': True, 'attempt': attempt}
    replay(s, packet, reserved)
    assert s.count() == 0
    mark('exact_replay')
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == attempt
    first, tail = attempt['runs']
    first_packet, recorded = s.step('run', attempt, run_id=first['run_id'])
    assert recorded['status'] == 'run_recorded' and s.count() == 1
    replay(s, first_packet, recorded)
    mark('reserved_unstarted_continuation')
    prefix = deepcopy(recorded['attempt']['runs'][0])
    assert recorded['attempt']['runs'][1] == tail and not tail['started']
    _, tail_result = s.step('run', recorded['attempt'], run_id=tail['run_id'])
    assert tail_result['attempt']['runs'][0] == prefix and s.count() == 2
    s.finish(tail_result['attempt'])
    mark('prefix_tail_once')
    # Fail the actual receipt write after its real process has produced effects.
    lost = subject(root / 'record-failure', extra=True)
    _, attempt = lost.begin()
    lost.fault('T056_receipt_save', 'execution_records',
        DATA['receipt_write_fault_condition'])
    try:
        run_packet, response = lost.step('run', attempt,
                                       run_id=attempt['runs'][0]['run_id'], expected=3)
        assert response['status'] == 'pending' and lost.count('guard') == 1
    finally:
        lost.clear_fault('T056_receipt_save')
    _, recovered = lost.step('recover', attempt, run_id=attempt['runs'][0]['run_id'])
    assert recovered['status'] == 'run_recorded' and lost.count('guard') == 1
    receipt = recovered['attempt']['runs'][0]['receipt']
    assert_receipt(receipt, lost.contract['methods'][0], dict(attempt, _cwd=str(lost.git_root)),
        attempt['runs'][0], lost.source())
    _, continued = lost.step('run', recovered['attempt'], run_id=attempt['runs'][1]['run_id'])
    lost.finish(continued['attempt'])
    assert lost.count() == 2
    mark('record_failure_recover_once')
    # The gates, previous proof and approved restart all exist before the live run.
    for transition in ('rework', 'advance', 'restart'):
        live = subject(root / ('live-' + transition), extra=True)
        proof = live.observe(live.frame)
        live.act('present', context_id=live.frame['context_id'], candidate_id=live.frame['candidate_id'])
        proposal, approval = live.restart_intent()
        _, attempt = live.begin()
        before_source = live.source()
        with live.live_run(attempt, attempt['runs'][0]) as running:
            abandon = {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
                **live.common(), 'action': 'abandon', 'attempt_id': attempt['attempt_id'],
                'reason': 'Сохранить фактический исход до новой работы.'}}
            live.deny(abandon, 'pending', 'unresolved', 'run', 'termination', 'quiescen')
            pending = live.lookup('attempt', attempt_id=attempt['attempt_id'])
            assert pending['runs'][0]['started'] and pending['runs'][0]['receipt'] is None
            assert pending['state'] == 'running'
            for packet in (
                live.packet('rework', target_stage_id='write', entry_tree=before_source['tree'],
                            reason='Повторить работу.'),
                live.packet('restart', proposal_id=proposal, approval_id=approval),
                live.outcome_packet(intent='cancel'),
            ):
                live.deny(packet, 'pending', 'unresolved', 'attempt', 'run')
            previous = rows(live.database)
            advance = live.packet_advance('next', actor='executor-A', proof={
                **{k: proof[k] for k in ('context_id', 'candidate_id', 'evidence_id')},
                'outcome': 'complete'})
            code, result = live.advance(advance)
            assert code == 2
            assert result['status'] == 'rejected'
            assert any(w in result['reason'].lower() for w in ('pending', 'attempt', 'run', 'unresolved'))
            assert rows(live.database) == previous and live.source() == before_source
            budget(live, 1, attempt['attempt_id'])
        assert running.process.returncode == 0 and running.response['status'] == 'run_recorded'
        if transition == 'rework':
            _, abandoned_live = live.step('abandon', running.response['attempt'],
                reason='Команда завершена, сохранить префикс без запуска хвоста.')
            assert abandoned_live['status'] == 'attempt_abandoned'
            assert abandoned_live['attempt']['runs'] == running.response['attempt']['runs']
            assert abandoned_live['attempt']['runs'][0]['termination'] == DATA['termination']
            assert abandoned_live['attempt']['runs'][1]['started'] is False
        else:
            _, tail = live.step('run', running.response['attempt'], run_id=attempt['runs'][1]['run_id'])
            live.finish(tail['attempt'])
        old = live.lookup('attempt', attempt_id=attempt['attempt_id'])
        if transition == 'rework':
            live.rework_current()
        elif transition == 'advance':
            live.next(proof, outcome='complete')
        else:
            live.act('restart', proposal_id=proposal, approval_id=approval)
            live.freeze()
            live.enter()
        budget(live, 0)
        assert live.lookup('attempt', attempt_id=attempt['attempt_id']) == old
    # The ordinary terminal decision also has an otherwise-valid positive control.
    for intent in ('accept', 'cancel'):
        terminal = subject(root / ('terminal-live-' + intent), variant='check')
        terminal.finish_route()
        _, attempt = terminal.begin()
        with terminal.live_run(attempt, attempt['runs'][0]) as running:
            terminal.deny(terminal.outcome_packet(intent=intent),
                          'pending', 'unresolved', 'attempt', 'run')
        assert running.process.returncode == 0
        current = running.response['attempt']
        assert_receipt(current['runs'][0]['receipt'], terminal.contract['methods'][0],
            dict(current, _cwd=str(terminal.git_root)), current['runs'][0], terminal.source(),
            guard=False)
        terminal.finish(current)
        terminal.record(terminal.outcome_packet(intent=intent),
                        kind='positive' if intent == 'accept' else 'cancelled')
    mark('live_pending_transitions')
    mark('actual_recovery_positive_transitions')
    # A quiescent started prefix plus an unstarted tail can be explicitly abandoned.
    abandoned = subject(root / 'abandon', extra=True)
    _, attempt = abandoned.begin()
    _, prefix_result = abandoned.step('run', attempt, run_id=attempt['runs'][0]['run_id'])
    _, result = abandoned.step('abandon', prefix_result['attempt'], reason='Сохранить завершённый префикс.')
    assert result['status'] == 'attempt_abandoned' and result['attempt']['state'] == 'abandoned'
    assert result['attempt']['runs'] == prefix_result['attempt']['runs']
    assert result['attempt']['runs'][0]['termination'] == DATA['termination']
    assert result['attempt']['runs'][1]['started'] is False
    assert abandoned.count() == 1
    old = result['attempt']
    abandoned.rework_current()
    assert abandoned.lookup('attempt', attempt_id=attempt['attempt_id']) == old
    mark('quiescent_abandon_retains_history')


def attempt_limits(root):
    s = subject(root / 'bound', maximum=2, extra=True)
    s.configure()
    _, first_finish, first = complete(s)
    first_response = {'schema': 'stride-execution/v1', 'status': 'attempt_finished',
        'revision': current_revision(s.database), 'replayed': False, 'changed': True, 'attempt': first}
    packet, second = s.begin(used=2)
    assert s.count() == 2
    budget(s, 2, second['attempt_id'])
    mark('new_attempt_below_bound')
    for _ in range(2):
        s.deny(s.begin_packet(), 'limit', 'attempt', 'budget')
    mark('exhausted_no_effect')
    _, result = s.step('run', second, run_id=second['runs'][0]['run_id'])
    _, result = s.step('run', result['attempt'], run_id=second['runs'][1]['run_id'])
    s.finish(result['attempt'])
    assert s.count() == 4
    mark('same_pending_resume_at_bound')
    before = budget(s, 2)
    replay(s, first_finish, first_response)
    for _ in range(2):
        assert s.lookup('attempt', attempt_id=first['attempt_id']) == first
        assert budget(s, 2) == before
        s.deny(s.begin_packet(), 'limit', 'attempt', 'budget')
    s.maximum = 3
    s.configure()
    _, _, third = complete(s, used=3)
    assert third['budget'] == {'used': 3, 'maximum': 3}
    assert first['budget'] == {'used': 1, 'maximum': 2}
    mark('read_replay_no_reset')
    s.perform('write')
    s.frame = s.gate_ref('review', tag='new-stage')
    budget(s, 0)
    _, empty = s.begin(actor='reviewer-B')
    s.finish(empty, actor='reviewer-B')
    budget(s, 1)
    mark('stage_reset')
    s.rework_current()
    budget(s, 0)
    _, empty = s.begin(actor='reviewer-B')
    s.finish(empty, actor='reviewer-B')
    s.restart_current()
    budget(s, 0)
    assert s.lookup('attempt', attempt_id=first['attempt_id']) == first
    mark('rework_restart_reset')
    from verification_checks import ReuseFamily
    family = ReuseFamily(root / 'reuse-bound', maximum=1)
    prepared, _ = prepare(family)
    verify_reuse(family, prepared)
    budget(family.target, 1)
    another, _ = prepare(family)
    packet = {'schema': 'stride-execution/v1', 'operation': 'reuse', 'input': {
        **family.target.common(), 'action': 'verify',
        'reuse_id': another['input']['reuse_id'], 'attempt_id': 'over-budget',
        'admission': family.admission()}}
    family.target.deny(packet, 'limit', 'budget', 'attempt')
    assert family.target.count() == 1
    mark('reuse_attempt_bound')


def relative_capture(root):
    s = subject(root / 'relative-config')
    foreign = root / 'unrelated-process-cwd'
    foreign.mkdir(parents=True)
    assert s.config.is_absolute() and foreign != s.config.parent and foreign != s.git_root
    settings = s.configure(repository=os.path.relpath(s.git_root, s.config.parent),
                           output_root=os.path.relpath(s.capture_root, s.config.parent))
    assert not Path(settings['repository']).is_absolute()
    assert not Path(settings['output_root']).is_absolute()
    assert s.contract['methods'][0]['cwd'] == '.'
    source = s.source()
    packet = s.begin_packet()
    code, reserved = raw(s.config, packet, cwd=foreign)
    assert code == 0 and reserved['status'] == 'attempt_reserved', reserved
    attempt = reserved['attempt']
    assert_attempt(attempt, subject=s, identifier=packet['input']['attempt_id'],
                   frame=s.frame, schedule=['guard'], used=1, maximum=s.maximum)
    run = attempt['runs'][0]
    packet = {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
        **s.common(), 'action': 'run', 'attempt_id': attempt['attempt_id'], 'run_id': run['run_id']}}
    code, response = raw(s.config, packet, cwd=foreign)
    assert code == 0 and response['status'] == 'run_recorded', response
    receipt = response['attempt']['runs'][0]['receipt']
    assert_receipt(receipt, s.contract['methods'][0], dict(attempt, _cwd=str(s.git_root)), run, source)
    assert receipt['cwd'] == '.' and receipt['resolved_cwd'] == str(s.git_root)
    assert Path(receipt['output_directory']).is_relative_to(s.capture_root)
    for item in receipt['outputs']:
        assert Path(item['path']).is_relative_to(s.capture_root)
        assert Path(item['path']).read_bytes() == bytes.fromhex(DATA['output_bytes'][item['id']])
    assert s.count() == 1 and s.source() == source and list(foreign.iterdir()) == []
    packet = {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
        **s.common(), 'action': 'finish', 'attempt_id': attempt['attempt_id'],
        'evidence_work': s.work_for(s.frame, [receipt])}}
    code, response = raw(s.config, packet, cwd=foreign)
    assert code == 0 and response['status'] == 'attempt_finished', response
    finished = response['attempt']
    assert_attempt(finished, subject=s, identifier=attempt['attempt_id'], frame=s.frame,
                   schedule=['guard'], used=1, maximum=s.maximum, state='finished')
    assert finished['runs'][0]['receipt'] == receipt
    before = rows(s.database)
    code, queried = raw(s.config, {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {
        'queries': [{'id': 'relative-read', 'kind': 'attempt', 'task_id': s.task_id,
                     'attempt_id': attempt['attempt_id']}]}}, cwd=foreign)
    assert code == 0 and queried == {'schema': 'stride-execution/v1', 'status': 'read_only',
        'revision': before['revision'], 'results': [
            {'id': 'relative-read', 'kind': 'attempt', 'result': finished}]}
    assert rows(s.database) == before and s.count() == 1 and list(foreign.iterdir()) == []


def input_binding(root):
    s = subject(root / 'binding')
    initial = json.loads(s.config.read_text())
    for case in DATA['config_denials']:
        config = deepcopy(initial)
        config['verification'][case['field']] = case['value']
        write_json(s.config, config)
        s.deny(s.begin_packet(), case['reason'])
        write_json(s.config, initial)
    mark('config_table')
    valid = s.begin_packet()
    for field in valid['input']:
        bad = deepcopy(valid)
        del bad['input'][field]
        s.deny(bad, field, 'input', 'field')
    bad = deepcopy(valid)
    bad['input']['unknown'] = 'unexpected'
    s.deny(bad, 'unknown', 'field', 'input')
    mark('input_table')
    variants = {
        'expected_revision': valid['input']['expected_revision'] - 1,
        'task_id': '0043', 'expected_generation': 2,
        'expected_contract_revision': 'not-current-contract',
        'context_id': 'not-current', 'candidate_id': 'not-current',
        'source': {'commit': '0' * 40, 'tree': '0' * 40}, 'method_ids': [],
    }
    for field, value in variants.items():
        bad = deepcopy(valid)
        bad['input'][field] = value
        s.deny(bad, field, 'revision', 'source', 'current', 'schedule', 'task', 'generation')
    mark('identity_source_method_table')
    s.configure(roles=['reviewer'])
    s.deny(s.begin_packet(), 'role', 'actor', 'writer')
    s.configure()
    bad = s.begin_packet(actor='unknown-actor')
    s.deny(bad, 'actor', 'role')
    mark('actor_policy')
    _, _, finished = complete(s)
    assert finished['runs'][0]['receipt']['passed'] is True and s.count() == 1
    relative_capture(root / 'relative')
    mark('corrected_positive_controls')
    del initial['verification']
    write_json(s.config, initial)
    before = rows(s.database)
    assert s.sync()['current_stage'] == 'write'
    assert rows(s.database) == before
    mark('old_read_without_capability')


def assert_reuse(value, family, packet, *, state, attempt=None, evidence=None, fact=None):
    s = family.target
    assert set(value) == set(DATA['reuse_keys'])
    expected = {'reuse_id': packet['input']['reuse_id'], 'task_id': s.task_id,
        'generation': s.generation, 'contract_revision': s.contract_revision,
        'source_task_id': '0042', 'source_outcome_id': family.source_fact['outcome_id'],
        'source': packet['input']['source'], 'context_id': packet['input']['context_id'],
        'candidate_id': packet['input']['candidate_id'],
        'artifact_mapping': packet['input']['artifact_mapping'], 'state': state,
        'attempt_id': attempt, 'evidence_id': evidence, 'fact': fact}
    assert value == expected, (value, expected)


def prepare(family, *, identifier=None, mapping=None):
    s = family.target
    packet = family.prepare_packet(identifier, mapping)
    cursor, source, old_count = s.sync(), s.source(), s.count()
    code, response = raw(s.config, packet)
    assert code == 0 and set(response) == {'schema', 'status', 'revision', 'replayed', 'changed', 'reuse'}
    assert response['status'] == 'reuse_prepared' and response['schema'] == 'stride-execution/v1'
    assert response['changed'] is True and response['replayed'] is False
    assert response['revision'] == current_revision(s.database)
    assert_reuse(response['reuse'], family, packet, state='prepared')
    assert s.lookup('reuse', reuse_id=packet['input']['reuse_id']) == response['reuse']
    assert s.sync() == cursor and s.source() == source and s.count() == old_count
    family.unchanged_source()
    return packet, response


def verify_reuse(family, prepared, *, attempt=None, expected=0, mode='good'):
    s = family.target
    identifier = prepared['input']['reuse_id']
    _, result = s.call('reuse', 'verify', expected=expected, reuse_id=identifier,
        attempt_id=attempt or s.identity('reuse-attempt'), admission=family.admission())
    assert set(result) == {'schema', 'status', 'revision', 'replayed', 'changed', 'reuse'}
    assert result['status'] == ('reuse_verified' if expected == 0 else
                               'checks_failed' if expected == 1 else 'pending')
    value = result['reuse']
    assert isinstance(value['attempt_id'], str) and value['attempt_id']
    current = s.lookup('attempt', attempt_id=value['attempt_id'])
    if expected in (0, 1):
        assert isinstance(value['evidence_id'], str) and value['evidence_id']
        assert_reuse(value, family, prepared, state='verified' if expected == 0 else 'checks_failed',
                     attempt=value['attempt_id'], evidence=value['evidence_id'])
        assert current['state'] == 'finished' and current['purpose'] == 'reuse'
        assert current['owner_id'] == identifier and current['evidence_id'] == value['evidence_id']
        assert current['source'] == prepared['input']['source']
        assert current['task_id'] == s.task_id and current['generation'] == s.generation
        scheduled = [m for m in s.contract['methods'] if m['verification_plan']['green_stages']]
        assert current['method_ids'] == [m['id'] for m in scheduled]
        assert len(current['runs']) == len(scheduled) and scheduled
        for method, run in zip(scheduled, current['runs'], strict=True):
            assert run['started'] is True and run['termination'] == DATA['termination']
            guard = method['id'] not in s.contract['evidence_plan'][s.frame['stage_id']]['subject_methods']
            assert_receipt(run['receipt'], method, dict(current, _cwd=str(s.git_root)), run,
                           prepared['input']['source'], mode=mode, guard=guard)
    family.unchanged_source()
    return result


def accept_packet(family, prepared, *, identifier=None):
    s = family.target
    return {'schema': 'stride-execution/v1', 'operation': 'reuse', 'input': {
        **s.common(), 'action': 'accept', 'reuse_id': prepared['input']['reuse_id'],
        'outcome_id': identifier or s.identity('reused-outcome'),
        'reason': 'Принять результат после свежей локальной проверки.',
        'result_commit': s.source()['commit'], 'admission': family.admission()}}


def accept_reuse(family, prepared, verified):
    s = family.target
    packet = accept_packet(family, prepared)
    cursor, physical = s.sync(), s.physical()
    source = family.source_fact
    provenance = {key: deepcopy(source['provenance'][key]) for key in (
        'visit_id', 'source_generation', 'source_actor_ids', 'gate_id', 'evidence_id')}
    provenance.update(current_gate_id=s.frame['gate_id'],
                      current_evidence_id=verified['reuse']['evidence_id'])
    expected = {'outcome_id': packet['input']['outcome_id'], 'task_id': s.task_id,
        'generation': s.generation, 'contract_revision': s.contract_revision,
        'requirements_snapshot_revision': s.initial_snapshot['snapshot_revision'],
        'kind': 'positive', 'stage_id': cursor['current_stage'], 'iteration': cursor['iteration'],
        'handler_outcome': None, 'source': prepared['input']['source'], 'provenance': provenance,
        'satisfied_task_requirements': ['report', 'zeta'], 'result_commit': s.source()['commit'],
        'decision': {'actor_id': 'executor-A', 'role_id': 'writer', 'intent': 'accept',
                     'reason': packet['input']['reason']},
        'reuse': {'source_task_id': '0042', 'source_outcome_id': source['outcome_id'],
                  'reuse_id': prepared['input']['reuse_id']}}
    assert set(expected) == set(DATA['ordinary_fact_keys']) | {'reuse'}
    code, result = raw(s.config, packet)
    assert code == 0 and result['status'] == 'reuse_accepted', result
    assert set(result) == {'schema', 'status', 'revision', 'replayed', 'changed', 'reuse'}
    assert result['replayed'] is False and result['changed'] is True
    assert result['revision'] == current_revision(s.database)
    assert_reuse(result['reuse'], family, prepared, state='accepted',
        attempt=verified['reuse']['attempt_id'], evidence=verified['reuse']['evidence_id'], fact=expected)
    assert s.sync() == cursor and s.physical() == physical
    s.sync()
    s.read_fact(value=expected)
    assert s.lookup('reuse', reuse_id=prepared['input']['reuse_id']) == result['reuse']
    family.unchanged_source()
    return packet, result


def related_reuse(root):
    from verification_checks import ReuseFamily, revision
    from outcome_checks import Delivery, requirements_packet, sequencing_packet
    family = ReuseFamily(root / 'family')
    s = family.target
    admission = family.admission()
    assert admission['family']['parent_id'] == '0042'
    assert [m['task']['task_id'] for m in admission['family']['members']] == ['0042', '0045']
    assert admission['eligibility'] == {'task_id': '0045', 'eligible': True,
                                      'blockers': [], 'result_provenance': []}
    assert family.source_fact['kind'] == 'positive' and len(family.source_fact) == 14
    assert s.initial_snapshot['snapshot_revision'] == 1
    assert family.source.initial_snapshot['snapshot_revision'] == 3
    assert s.contract['workspace_id'] != family.source.contract['workspace_id']
    assert s.contract['admission_id'] != family.source.contract['admission_id']
    assert s.initial_snapshot['registry_revision'] != family.source.initial_snapshot['registry_revision']
    assert s.initial_snapshot['links']['application_task'] != family.source.initial_snapshot['links']['application_task']
    assert s.git_root != family.source.git_root and s.source() == family.source.source()
    mark('real_family_and_source')
    prepared, response = prepare(family)
    mark('prepare_projection')
    cursor, count = s.sync(), s.count()
    verified = verify_reuse(family, prepared)
    assert s.count() == count + 1
    assert s.sync() == cursor
    mark('fresh_local_verification')
    accepted_packet, accepted = accept_reuse(family, prepared, verified)
    fact = accepted['reuse']['fact']
    mark('explicit_accept_full_fact')
    assert family.receiver.query('task', '0045')['state'] == 'claimed'
    coordinator = Delivery(root / 'delivery', s, family.receiver)
    config = json.loads(coordinator.config.read_text())
    config['commands']['execution']['accepted_exit_codes'] = [0, 1, 2, 3]
    write_json(coordinator.config, config)
    for command, packet in [('requirements', requirements_packet(s, fact))]:
        outer = coordinator.invoke(command, packet)['result']
        assert outer['disposition'] == 'returned' and outer['exit_code'] == 0
        assert outer['packet']['status'] == 'applied'
    family.source.workspace.apply([{'id': 'release-target', 'kind': 'release_claim',
        'task_id': '0045', 'claim_id': family.claim_id, 'claimant': family.claimant,
        'reason': 'Передать принятый результат.'}])
    outer = coordinator.invoke('sequencing', sequencing_packet(family.receiver, fact))['result']
    assert outer['disposition'] == 'returned' and outer['exit_code'] == 0
    assert outer['packet']['status'] == 'applied'
    target = family.receiver.query('task', '0045')
    assert target['state'] == 'completed' and target['claim'] is None
    assert target['terminal']['result_commit'] == fact['result_commit']
    with __import__('sqlite3').connect(s.requirements.database) as db:
        actual = db.execute('SELECT outcome_id,task_id,snapshot_revision,kind,keys '
            'FROM requirement_outcomes WHERE outcome_id=?', (fact['outcome_id'],)).fetchall()
    assert [(a, b, c, d, json.loads(e)) for a, b, c, d, e in actual] == [(
        fact['outcome_id'], '0045', 1, 'positive', ['report', 'zeta'])]
    from verification_checks import bind
    satisfaction = s.requirements.invoke({'operation': 'query', 'input': {'queries': [{
        'id': 'satisfaction', 'kind': 'current_satisfaction'}]}})
    expected_satisfaction = bind(DATA['family_satisfaction'], {
        '@SOURCE_OUTCOME': family.source_fact['outcome_id'], '@TARGET_OUTCOME': fact['outcome_id']})
    assert satisfaction == {'status': 'read_only',
        'revision': revision(s.requirements.database, 'requirements'),
        'results': [{'id': 'satisfaction', 'value': expected_satisfaction}]}
    mark('real_both_receivers')
    assert s.sync() == cursor
    family.unchanged_source()
    replay(s, accepted_packet, accepted)
    mark('source_unchanged_cursor_not_advanced')


def change(case):
    def apply(value):
        target = value
        for key in case['path'][:-1]:
            target = target[key]
        target[case['path'][-1]] = deepcopy(case['value'])
    apply.parent_case = case['id'] == 'parent'
    return apply


def compatibility(root):
    from verification_checks import ReuseFamily
    compatible = ReuseFamily(root / 'compatible')
    prepared, _ = prepare(compatible)
    verify_reuse(compatible, prepared)
    mark('identity_only_compatible')
    for case in DATA['statement_differences']:
        family = ReuseFamily(root / ('statement-' + case['id']), target_change=change(case))
        family.target.deny(family.prepare_packet(), 'compatib', 'snapshot', 'requirement', 'contract')
        family.unchanged_source()
    for case in DATA['same_parent_differences']:
        family = ReuseFamily(root / case['id'], same_parent_change=case)
        source = family.source.initial_snapshot
        target = family.target.initial_snapshot
        expected_parent = dict(DATA['same_parent_baseline'], **{case['field']: case['value']})
        assert source['requirements']['APP-REPORT'] == DATA['same_parent_baseline']
        assert target['requirements']['APP-REPORT'] == expected_parent
        assert source['requirements']['SYS-REPORT'] == target['requirements']['SYS-REPORT']
        assert source['links']['system_application'] == target['links']['system_application']
        for key in ('goal', 'task_requirements', 'definition_of_done'):
            assert source['statement'][key] == target['statement'][key]
        own_links = lambda snapshot: [(v['application'], v['key'])
                                     for v in snapshot['links']['application_task']]
        assert own_links(source) == own_links(target)
        family.target.deny(family.prepare_packet(), 'compatib', 'snapshot', 'requirement', 'parent', 'contract')
        family.unchanged_source()
    # Keep the genuine same-parent compatible pair as the matched positive control.
    assert compatible.source.initial_snapshot['requirements']['APP-REPORT'] == DATA['same_parent_baseline']
    assert compatible.target.initial_snapshot['requirements']['APP-REPORT'] == DATA['same_parent_baseline']
    valid, _ = prepare(compatible)
    verify_reuse(compatible, valid)
    mark('substantive_snapshot_table')
    for case in DATA['contract_differences']:
        family = ReuseFamily(root / ('contract-' + case['id']), target_contract_change=change(case))
        family.target.deny(family.prepare_packet(), 'compatib', 'contract', 'method', 'evidence', 'schedule')
        family.unchanged_source()
    process = ReuseFamily(root / 'changed-process', target_process_change={
        'instruction': 'Другой согласованный порядок выполнения этапа.'})
    process.target.deny(process.prepare_packet(), 'compatib', 'process', 'contract')
    process.unchanged_source()
    mark('process_evidence_method_table')
    for name, options in [('unrelated', {'related': False}),
                          ('unfinished', {'deliver_source': False}),
                          ('cancelled', {'source_kind': 'cancelled'}),
                          ('null-commit', {'result_commit': False})]:
        family = ReuseFamily(root / name, **options)
        family.target.deny(family.prepare_packet(), 'source', 'positive', 'completed', 'family', 'commit')
        family.unchanged_source()
    mark('unrelated_unfinished_nonpositive_table')
    s = compatible.target
    original = json.loads(s.config.read_text())
    s.configure(base_ref='refs/heads/absent-integration')
    s.deny(compatible.prepare_packet(), 'commit', 'ref', 'integration', 'source')
    write_json(s.config, original)
    # The new local target may be a real descendant, not just the original commit.
    s.git('commit', '--allow-empty', '-m', 'Prepare a local descendant')
    s.frame = s.gate_ref('write', tag='descendant')
    prepared, _ = prepare(compatible)
    verified = verify_reuse(compatible, prepared)
    assert verified['reuse']['source']['commit'] != compatible.source_fact['result_commit']
    assert s.git('merge-base', '--is-ancestor', compatible.source_fact['result_commit'],
                 s.source()['commit']) == ''
    unavailable = ReuseFamily(root / 'unavailable-local-history')
    local = unavailable.target
    local.git('checkout', '--orphan', 'isolated-local-history')
    local.git('commit', '-m', 'Prepare independent local history')
    local.frame = local.gate_ref('write', tag='independent-history')
    local.deny(unavailable.prepare_packet(), 'ancestor', 'local', 'history', 'source')
    mark('source_available_local_ancestor')
    old = deepcopy(compatible.source_fact)
    compatible.source.restart_current()
    assert compatible.source.generation == 2 and old['generation'] == 1
    compatible.source_records = deepcopy(compatible.source.records())
    compatible.source_head = compatible.source.source()
    compatible.source_publication = deepcopy(compatible.source.initial_snapshot)
    prepared, _ = prepare(compatible)
    verified = verify_reuse(compatible, prepared)
    _, accepted = accept_reuse(compatible, prepared, verified)
    assert accepted['reuse']['fact']['provenance']['source_generation'] == 1
    compatible.source.sync()
    compatible.source.read_fact(value=old)
    mark('historical_source_after_restart')


def file_mapping(root):
    from verification_checks import ReuseFamily
    family = ReuseFamily(root / 'mapped', artifact_case=True)
    s = family.target
    mapping = [{'source_scope': scope, 'source_path': '0042/result.' + suffix,
                'target_scope': scope, 'target_path': '0045/result.' + suffix}
               for scope, suffix in [('task', 'txt'), ('sprint', 'bin')]]
    before = {str(path): Path(path).read_bytes() for path in family.source.selected_files}
    for item in mapping:
        source = s.file_roots[item['source_scope']] / item['source_path']
        target = s.file_roots[item['target_scope']] / item['target_path']
        assert source.read_bytes() == target.read_bytes() == bytes.fromhex(
            DATA['artifact_bytes'][item['source_scope']])
    mark('text_binary_mapping')
    prepared, response = prepare(family, mapping=mapping)
    assert response['reuse']['artifact_mapping'] == mapping
    assert {row['source_scope'] for row in mapping} == {'task', 'sprint'}
    mark('task_sprint_scope')
    verified = verify_reuse(family, prepared)
    attempt = s.lookup('attempt', attempt_id=verified['reuse']['attempt_id'])
    assert len(attempt['runs'][0]['receipt']['outputs']) == 2
    assert all(row['required'] and row['status'] == 'captured'
               for row in attempt['runs'][0]['receipt']['outputs'])
    accept_reuse(family, prepared, verified)
    mark('required_outputs')
    partial = ReuseFamily(root / 'partial', artifact_case=True)
    t = partial.target
    sentinel = t.file_roots['task'] / '0045/unrelated.txt'
    sentinel.write_bytes(b'Preserved local file.\n')
    missing = Path(t.selected_files[1])
    missing.unlink()
    negative = t.gate_ref('write', tag='partial-delivery', files=[t.selected_files[0]], accepted=False)
    assert negative['accepted'] is False
    assert any(not row['passed'] for row in negative['assessment']['artifact_checks'])
    # Caller completes the physical delivery, then asks the existing Gate owner.
    source = Path(partial.source.selected_files[1])
    missing.write_bytes(source.read_bytes())
    t.frame = t.gate_ref('write', tag='completed-delivery')
    packet, _ = prepare(partial, mapping=mapping)
    verified = verify_reuse(partial, packet)
    accept_reuse(partial, packet, verified)
    assert sentinel.read_bytes() == b'Preserved local file.\n'
    assert source.read_bytes() == bytes.fromhex(DATA['artifact_bytes']['sprint'])
    for path, value in before.items():
        assert Path(path).read_bytes() == value
    mark('partial_delivery_recovery_source_unchanged')
    required = ReuseFamily(root / 'missing-output')
    required.target.mode('missing')
    prepared, _ = prepare(required)
    result = verify_reuse(required, prepared, expected=1, mode='missing')
    assert result['reuse']['fact'] is None
    required.target.deny(accept_packet(required, prepared), 'verif', 'check', 'failed')
    optional = ReuseFamily(root / 'optional-output', output_policy='optional')
    optional.target.mode('missing')
    prepared, _ = prepare(optional)
    verify_reuse(optional, prepared, mode='missing_optional')
    empty = ReuseFamily(root / 'no-declared-output', output_policy='empty')
    prepared, _ = prepare(empty)
    verify_reuse(empty, prepared, mode='no_outputs')
    assert prepared['input']['artifact_mapping'] == []
    mark('required_missing_vs_optional_empty')


def finality_case(root, first, *, second_reuse=False, interphase=False):
    from verification_checks import ReuseFamily
    family = ReuseFamily(root)
    s = family.target
    # All conflicting results share a genuinely valid admitted terminal context.
    s.finish_route()
    prepared, _ = prepare(family)
    if interphase:
        ordinary = s.record(s.outcome_packet(intent=first),
                            kind='cancelled' if first == 'cancel' else 'positive')
        saved = deepcopy(ordinary['fact'])
        packet = {'schema': 'stride-execution/v1', 'operation': 'reuse', 'input': {
            **s.common(), 'action': 'verify', 'reuse_id': prepared['input']['reuse_id'],
            'attempt_id': s.identity('competing-attempt'), 'admission': family.admission()}}
        s.deny(packet, 'final', 'result', 'outcome', 'closed')
        s.sync()
        s.read_fact(value=saved)
        return family
    verified = verify_reuse(family, prepared)
    other, other_verified = None, None
    if second_reuse:
        other, _ = prepare(family)
        other_verified = verify_reuse(family, other)
    if first in ('accept', 'cancel'):
        packet = s.outcome_packet(intent=first)
        ordinary = s.record(packet, kind='cancelled' if first == 'cancel' else 'positive')
        s.deny(accept_packet(family, prepared), 'final', 'result', 'outcome', 'closed')
        s.sync()
        s.read_fact(value=ordinary['fact'])
        replay(s, packet, ordinary)
    else:
        packet, accepted = accept_reuse(family, prepared, verified)
        saved = deepcopy(accepted['reuse']['fact'])
        for intent in ('accept', 'cancel'):
            s.deny(s.outcome_packet(intent=intent), 'final', 'result', 'outcome', 'closed')
        if other is not None:
            assert other_verified['reuse']['state'] == 'verified'
            s.deny(accept_packet(family, other), 'final', 'result', 'outcome', 'closed')
        s.sync()
        s.read_fact(value=saved)
        replay(s, packet, accepted)
    assert family.receiver.query('task', '0045')['state'] == 'claimed'
    family.unchanged_source()
    return family


def live_reuse_decisions(root):
    from verification_checks import ReuseFamily
    family = ReuseFamily(root, variant='check')
    s = family.target
    s.finish_route()
    first, _ = prepare(family)
    second, prepared_second = prepare(family)
    verified_first = verify_reuse(family, first)
    saved_attempt = s.lookup('attempt', attempt_id=verified_first['reuse']['attempt_id'])
    saved_files = {item['path']: Path(item['path']).read_bytes()
                   for run in saved_attempt['runs'] for item in run['receipt']['outputs']}
    assert len(saved_files) == 2
    before_count = s.count()
    _, attempt = s.begin(used=2)
    cursor, source = s.sync(), s.source()
    with s.live_run(attempt, attempt['runs'][0]) as running:
        unknown = s.lookup('attempt', attempt_id=attempt['attempt_id'])
        assert unknown['state'] == 'running' and unknown['runs'][0]['started'] is True
        assert unknown['runs'][0]['receipt'] is None and unknown['runs'][0]['termination'] is None
        budget(s, 2, attempt['attempt_id'])
        verification = {'schema': 'stride-execution/v1', 'operation': 'reuse', 'input': {
            **s.common(), 'action': 'verify', 'reuse_id': second['input']['reuse_id'],
            'attempt_id': s.identity('blocked-new-reuse-attempt'), 'admission': family.admission()}}
        s.deny(verification, 'pending', 'unresolved', 'attempt', 'run')
        s.deny(accept_packet(family, first), 'pending', 'unresolved', 'attempt', 'run')
        assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == unknown
        assert s.lookup('reuse', reuse_id=first['input']['reuse_id']) == verified_first['reuse']
        assert s.lookup('reuse', reuse_id=second['input']['reuse_id']) == prepared_second['reuse']
        assert s.sync() == cursor and s.source() == source and s.count() == before_count
        assert {path: Path(path).read_bytes() for path in saved_files} == saved_files
        budget(s, 2, attempt['attempt_id'])
    assert running.process.returncode == 0 and running.response['status'] == 'run_recorded'
    assert s.count() == before_count + 1
    _, resolved = s.finish(running.response['attempt'])
    assert resolved['state'] == 'finished' and resolved['runs'][0]['termination'] == DATA['termination']
    verified_second = verify_reuse(family, second)
    assert verified_second['reuse']['state'] == 'verified' and s.count() == before_count + 2
    budget(s, 3)
    accept_reuse(family, first, verified_first)
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == resolved
    assert {path: Path(path).read_bytes() for path in saved_files} == saved_files
    assert s.sync() == cursor and s.source() == source and s.count() == before_count + 2
    family.unchanged_source()


def failed_reuse(root):
    from verification_checks import ReuseFamily
    family = ReuseFamily(root / 'failed', maximum=2)
    s = family.target
    s.mode('bad')
    prepared, _ = prepare(family)
    failed = verify_reuse(family, prepared, expected=1, mode='bad')
    history = s.lookup('attempt', attempt_id=failed['reuse']['attempt_id'])
    assert history['runs'][0]['receipt']['actual_exit_code'] == 7
    assert history['runs'][0]['receipt']['passed'] is False
    s.deny(accept_packet(family, prepared), 'verif', 'check', 'failed')
    assert family.receiver.query('task', '0045')['state'] == 'claimed'
    mark('fresh_failed_guard_no_old_pass')
    s.mode('good')
    prepared, _ = prepare(family)
    verified = verify_reuse(family, prepared)
    assert s.count() == 2
    assert s.lookup('attempt', attempt_id=failed['reuse']['attempt_id']) == history
    mark('local_repair_fresh_attempt')
    # Same budget belongs to ordinary verification and reuse reservation.
    s.deny(s.begin_packet(), 'budget', 'limit', 'attempt')
    s.git('commit', '--allow-empty', '-m', 'Change current local source after verification')
    s.deny(accept_packet(family, prepared), 'source', 'commit', 'changed', 'current')
    assert s.lookup('attempt', attempt_id=failed['reuse']['attempt_id']) == history
    mark('drift_after_verify')
    # Unknown reuse uses the same durable attempt, not a replacement command.
    unknown = ReuseFamily(root / 'unknown', maximum=1)
    t = unknown.target
    prepared, _ = prepare(unknown)
    t.fault('T056_reuse_receipt', 'execution_records',
        DATA['receipt_write_fault_condition'])
    attempt_id = t.identity('unknown-reuse-attempt')
    try:
        pending = verify_reuse(unknown, prepared, attempt=attempt_id, expected=3)
        assert pending['reuse']['state'] == 'verifying' and t.count() == 1
    finally:
        t.clear_fault('T056_reuse_receipt')
    resumed = verify_reuse(unknown, prepared, attempt=attempt_id)
    assert resumed['reuse']['attempt_id'] == attempt_id and t.count() == 1
    budget(t, 1)
    live_reuse_decisions(root / 'live-reuse-decisions')
    mark('unknown_reuse_continue')
    for intent in ('accept', 'cancel'):
        finality_case(root / ('ordinary-first-' + intent), intent)
    mark('ordinary_then_reuse')
    new_work = finality_case(root / 'reuse-first', 'reuse', second_reuse=True)
    mark('reuse_then_ordinary_and_second')
    for intent in ('accept', 'cancel'):
        finality_case(root / ('interphase-' + intent), intent, interphase=True)
    mark('interphase_finality')
    t = new_work.target
    old_facts = [r for r in t.records() if r['kind'] == 'outcome_fact']
    assert len(old_facts) == 1
    t.rework_current()
    prepared, _ = prepare(new_work)
    verified = verify_reuse(new_work, prepared)
    accept_reuse(new_work, prepared, verified)
    facts = [r for r in t.records() if r['kind'] == 'outcome_fact']
    assert len(facts) == 2 and facts[0] == old_facts[0]
    t.restart_current()
    prepared, _ = prepare(new_work)
    verified = verify_reuse(new_work, prepared)
    accept_reuse(new_work, prepared, verified)
    assert len([r for r in t.records() if r['kind'] == 'outcome_fact']) == 3
    mark('new_work_opens_shared_scope')


def addressed_history(root):
    from verification_checks import ReuseFamily
    family = ReuseFamily(root / 'history')
    s = family.target
    prepared, response = prepare(family)
    replay(s, prepared, response)
    # Equal business identity through a fresh request is not another reservation.
    same = deepcopy(prepared)
    same['input'].update(**s.common())
    code, duplicate = raw(s.config, same)
    assert code == 0 and duplicate['reuse'] == response['reuse'] and not duplicate['changed']
    assert s.count() == 0
    conflicting = deepcopy(same)
    conflicting['input'].update(**s.common())
    conflicting['input']['candidate_id'] = 'conflicting-candidate'
    s.deny(conflicting, 'conflict', 'identity', 'reuse', 'candidate')
    conflicting = deepcopy(same)
    conflicting['input'].update(**s.common())
    conflicting['input']['source_outcome_id'] = 'different-result'
    s.deny(conflicting, 'conflict', 'identity', 'source', 'reuse')
    mark('equal_exact_conflicting_identity')
    verified = verify_reuse(family, prepared)
    attempt = s.lookup('attempt', attempt_id=verified['reuse']['attempt_id'])
    assert s.lookup('reuse', reuse_id=prepared['input']['reuse_id']) == verified['reuse']
    budget(s, 1)
    # Each raw CLI invocation creates a new process and reopens the same store.
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == attempt
    mark('full_query_reopen')
    packet = accept_packet(family, prepared)
    s.fault('T056_outcome_atomic', 'execution_records', "NEW.kind='outcome_fact'")
    try:
        before, count = rows(s.database), s.count()
        code, rejected = raw(s.config, packet)
        assert code == 2 and rejected['status'] == 'rejected'
        assert 'T056 injected record boundary' in rejected['reason']
        assert rows(s.database) == before and s.count() == count
    finally:
        s.clear_fault('T056_outcome_atomic')
    accepted_packet, accepted = accept_reuse(family, prepared, verified)
    mark('local_atomic_failure')
    historical = deepcopy(accepted['reuse'])
    s.restart_current()
    assert s.generation == 2
    assert s.lookup('reuse', reuse_id=prepared['input']['reuse_id']) == historical
    assert s.lookup('attempt', attempt_id=attempt['attempt_id']) == attempt
    s.sync()
    s.read_fact(value=historical['fact'])
    mark('retained_history')
    replay(s, accepted_packet, accepted)
    prepared, _ = prepare(family)
    verified = verify_reuse(family, prepared)
    _, current = accept_reuse(family, prepared, verified)
    assert current['reuse']['fact']['generation'] == 2
    assert historical['fact']['generation'] == 1
    assert current['reuse']['fact']['outcome_id'] != historical['fact']['outcome_id']
    mark('historical_replay_does_not_close_new_work')


GROUPS = [verification, recovery, attempt_limits, input_binding, related_reuse,
          compatibility, file_mapping, failed_reuse, addressed_history]


def main():
    requested = sys.argv[1:]
    assert not requested or requested == ['--suite', 'behavior'], requested
    directory = Path(os.environ.get('POISE_RUN_OUTPUT_DIR')
                     or tempfile.mkdtemp(prefix='execution-verification-'))
    directory.mkdir(parents=True, exist_ok=True)
    cases, missing, errors = [], [], []
    for (name, expected), check in zip(DATA['groups'].items(), GROUPS, strict=True):
        start = len(LOG)
        try:
            check(directory / name)
            actual = [row['subcheck'] for row in LOG[start:] if 'subcheck' in row]
            assert len(actual) == len(set(actual)) and set(actual) == set(expected), (actual, expected)
            cases.append({'group': name, 'status': 'passed', 'subchecks': actual})
        except MissingVerification:
            missing.append(name)
            cases.append({'group': name, 'status': 'missing-interface',
                          'reason': DATA['missing_operation']})
        except Exception:
            trace = traceback.format_exc()
            errors.append({'group': name, 'traceback': trace})
            cases.append({'group': name, 'status': 'failed', 'traceback': trace})
    expected_missing = list(DATA['groups'])
    intended_red = missing == expected_missing and not errors
    status = 'passed' if not missing and not errors else 'expected-missing-interface' if intended_red else 'failed'
    report = {'status': status, 'cases': cases, 'missing': missing, 'errors': errors,
              'intended_red': intended_red, 'logs': LOG}
    (directory / 'verification-reuse-details.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'groups': len(cases), 'missing': len(missing),
                      'errors': len(errors), 'intended_red': intended_red}, ensure_ascii=False))
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
