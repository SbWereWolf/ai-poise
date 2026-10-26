#!/usr/bin/env python3
"""Seven finite public cleanup groups with independent physical-owner effects."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import traceback

from cleanup_checks import (APP, DATA, LOG, CleanupSubject, MissingCleanup, bind, git,
    invoke, mark, mutate, original_records, physical, revision, rows, write_json)

ORDER = [('remove', 'worktree'), ('remove', 'branch'),
         ('remove', 'temporary'), ('remove', 'temporary_backup')]


def wait_for_decision(s, cleanup, resources):
    assert cleanup['status'] == 'disposition_required'
    assert cleanup['disposition'] is None and cleanup['work'] is None
    assert cleanup['resources'] == resources and cleanup['removed'] == []
    assert isinstance(cleanup['issue']['reason'], str) and cleanup['issue']['reason']
    assert isinstance(cleanup['issue']['recovery'], str) and cleanup['issue']['recovery']
    assert s.effect_observation() == (True, True, True, True, 'Separate owner data.\n')
    assert s.owner_state()['effects'] == {} and s.owner_state()['calls'] == []


def terminal_decisions(root):
    s = CleanupSubject(root / 'terminal')
    cleanup = s.prepare(disposition=None)
    wait_for_decision(s, cleanup, s.resources)
    assert cleanup['history'][0]['event'] == 'obligation_recorded'
    assert all(event['observation'] is None for event in cleanup['history'])
    assert s.query() == cleanup
    mark('actual_cancelled_outcome_link_and_disposition_required')
    empty = CleanupSubject(root / 'empty')
    done = empty.prepare(resources=[], disposition=None)
    assert done['status'] == 'cleanup_complete' and done['work'] is None
    assert done['resources'] == [] and done['removed'] == []
    assert empty.query() == done and empty.owner_state()['calls'] == []
    assert empty.effect_observation() == (True, True, True, True, 'Separate owner data.\n')
    mark('resource_free_complete')
    active = CleanupSubject(root / 'active', terminal=False)
    active.refuse(active.packet('prepare'))
    other = CleanupSubject(root / 'other')
    neighbor = deepcopy(DATA['contract'])
    neighbor['task_id'] = '0043'
    neighbor['workspace_id'] = 'ws-neighbor'
    setup = bind(DATA['setup'], {'@CONTRACT': neighbor})
    setup['input']['request_id'] = 'neighbor-setup'
    setup['input']['expected_revision'] = revision(other.database)
    code, added = invoke(other.config, setup)
    assert code == 0 and added['status'] == 'applied'
    cancellation = bind(DATA['cancel'], {
        '@REVISION': revision(other.database),
        '@CONTRACT_REVISION': added['results'][0]['contract_revision']})
    cancellation['input']['task_id'] = '0043'
    cancellation['input']['request_id'] = 'neighbor-cancel'
    cancellation['input']['outcome_id'] = 'neighbor-outcome'
    code, actual_neighbor = invoke(other.config, cancellation)
    assert code == 0 and actual_neighbor['fact']['task_id'] == '0043'
    packet = other.packet('prepare')
    packet['input']['outcome_id'] = 'neighbor-outcome'
    other.refuse(packet)
    packet = other.packet('prepare')
    packet['input']['actor_id'] = 'reviewer-B'
    other.refuse(packet)
    mark('nonterminal_or_foreign_outcome_and_wrong_role_refused')
    for field, bad in [('expected_generation', 2), ('expected_contract_revision', 'other-contract')]:
        packet = other.packet('prepare')
        packet['input'][field] = bad
        other.refuse(packet)
    packet = other.packet('prepare')
    packet['input']['resources'][0]['reference']['workspace_id'] = 'unrelated-workspace'
    other.refuse(packet)
    mark('current_generation_contract_and_workspace_binding_match')
    for resource_id in ('worktree', 'temporary'):
        branchless = CleanupSubject(root / ('branchless-' + resource_id))
        resources = [value for value in branchless.resources if value['resource_id'] == resource_id]
        waiting = branchless.prepare(resources=resources, disposition=None)
        wait_for_decision(branchless, waiting, resources)
        discard = {'kind': 'discard_authorized', 'expected_commit': None}
        ready = branchless.proceed(disposition=discard)
        assert ready['disposition'] == discard
        branchless.drain(ready, [('remove', resource_id)])
        assert branchless.effect_observation()[1] is True
    mark('branchless_nonempty_null_waits_then_explicit_discard_null_commit')


def dispositions(root):
    s = CleanupSubject(root / 'preserved')
    s.disposition = {'kind': 'preserved', 'expected_commit': s.commit}
    cleanup = s.prepare()
    assert cleanup['work']['operation'] == 'preserve'
    assert s.effect_observation()[:4] == (True, True, True, True)
    observed = physical(s.owner_root, cleanup['work'])
    assert observed['status'] == 'succeeded' and observed['result']['effect'] == 'preserved'
    bundle = Path(observed['result']['preservation_ref'])
    assert bundle.is_file() and s.commit in git(s.owner_root / 'repository', 'bundle', 'list-heads', str(bundle))
    cleanup = s.observe(cleanup['work'], observed)
    done, _ = s.drain(cleanup, ORDER)
    assert s.owner_state()['effects']['preserve:branch'] == 1 and bundle.is_file()
    assert done['disposition'] == s.disposition
    mark('preserve_owner_receipt_precedes_any_remove')
    integrated = CleanupSubject(root / 'integrated')
    integrated.disposition = bind(DATA['dispositions']['integrated'], {'@COMMIT': integrated.commit})
    cleanup = integrated.prepare()
    assert cleanup['work']['operation'] == 'integration_status'
    observation = physical(integrated.owner_root, cleanup['work'])
    for mismatch in DATA['integration_fact_mismatches']:
        bad = deepcopy(observation)
        bad['result'][mismatch['field']] = mismatch['value']
        integrated.refuse(integrated.packet('observe', work=cleanup['work'], observation=bad))
        assert integrated.query() == cleanup
    cleanup = integrated.observe(cleanup['work'], observation)
    integrated.drain(cleanup, ORDER)
    assert integrated.owner_state()['effects']['integration_status:branch'] == 1
    mark('integrated_owner_fact_matches_selected_commit_and_request')
    discarded = CleanupSubject(root / 'discarded')
    cleanup = discarded.prepare()
    assert cleanup['work']['operation'] == 'remove'
    discarded.drain(cleanup, ORDER)
    assert set(discarded.owner_state()['effects']) == {
        'remove:worktree', 'remove:branch', 'remove:temporary', 'remove:temporary_backup'}
    mark('explicit_discard_needs_no_preservation')
    selected = CleanupSubject(root / 'selected')
    waiting = selected.prepare(disposition=None)
    wait_for_decision(selected, waiting, selected.resources)
    ready = selected.proceed()
    assert ready['disposition'] == selected.disposition and ready['work']['mode'] == 'execute'
    packet = selected.packet('continue', disposition={'kind': 'preserved', 'expected_commit': selected.commit})
    selected.refuse(packet)
    mark('initial_disposition_selection_and_immutable_replay')


def ordered_effects(root):
    s = CleanupSubject(root / 'ordered')
    before_rows, before_owner = rows(s.database), s.owner_state()
    supplied = [s.resources[1], s.resources[0], s.resources[2], s.resources[3]]
    cleanup = s.prepare(resources=supplied)
    assert s.owner_state() == before_owner and s.effect_observation()[:4] == (True, True, True, True)
    query_before = rows(s.database)
    assert s.query() == cleanup and rows(s.database) == query_before
    assert before_rows != query_before
    mark('prepare_and_query_have_no_physical_effect')
    assert cleanup['resources'] == supplied
    assert cleanup['work']['resource']['resource_id'] == 'worktree'
    mark('worktree_before_branch_even_from_reversed_input')
    done, operations = s.drain(cleanup, ORDER)
    assert operations == ORDER and s.effect_observation()[:4] == (False, False, False, False)
    assert [item['resource_id'] for item in done['removed']] == ['worktree', 'branch', 'temporary', 'temporary_backup']
    assert all(value == 1 for value in s.owner_state()['effects'].values())
    mark('actual_owner_removes_all_four_resource_kinds')
    assert s.foreign.read_text() == 'Separate owner data.\n'
    assert revision(s.database) == 7
    assert len(done['history']) == 6
    assert [event['event'] for event in done['history']] == [
        'obligation_recorded', 'disposition_recorded', 'owner_observed',
        'owner_observed', 'owner_observed', 'owner_observed']
    s.preserved_original_records()
    mark('own_state_changes_follow_real_observation_and_foreign_markers_remain')


def failure_resume(root):
    s = CleanupSubject(root / 'partial')
    cleanup = s.prepare(resources=s.resources[:2])
    s.owner_setting(fail_once='branch')
    first = cleanup['work']
    cleanup = s.observe(first, physical(s.owner_root, first))
    failed_work = cleanup['work']
    failure = physical(s.owner_root, failed_work)
    assert failure['status'] == 'failed'
    cleanup = s.observe(failed_work, failure)
    assert cleanup['status'] == 'cleanup_blocked'
    assert cleanup['issue'] == failure['result']
    assert cleanup['history'][-1]['observation'] == failure
    mark('known_failure_retains_reason_remaining_work_and_receipt')
    assert cleanup['resources'] == [s.resources[1]] and cleanup['removed'] == [s.resources[0]]
    assert s.effect_observation()[:2] == (False, True)
    assert s.owner_state()['effects'] == {'remove:worktree': 1}
    mark('partial_worktree_success_branch_failure_persisted')
    assert s.query() == cleanup
    resumed = s.proceed()
    assert resumed['work']['step_id'] == failed_work['step_id']
    assert resumed['work']['attempt_id'] != failed_work['attempt_id']
    assert resumed['work']['mode'] == 'execute'
    done, _ = s.drain(resumed, [('remove', 'branch')])
    assert s.owner_state()['effects'] == {'remove:worktree': 1, 'remove:branch': 1}
    mark('new_process_query_and_explicit_retry_finish_remaining_only')
    assert done['removed'] == s.resources[:2]
    assert sum(event['observation'] == failure for event in done['history']) == 1
    assert any(event['event'] == 'retry_requested' for event in done['history'])
    mark('old_failure_and_removed_history_retained')


def lost_caller(root, position):
    s = CleanupSubject(root)
    discard = {'kind': 'discard_authorized', 'expected_commit': None}
    packet = s.packet('prepare', resources=[s.resources[2]], disposition=discard)
    path = root / 'caller-request.json'
    write_json(path, packet)
    process = subprocess.run([sys.executable, '-B', str(APP / 'tests/cleanup_checks.py'),
        '--caller-loss', str(s.config), str(s.owner_root), str(path), position],
        capture_output=True, text=True)
    LOG.append({'boundary': 'actual-caller-loss', 'position': position,
                'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr})
    assert process.returncode == 42 and process.stdout == '' and process.stderr == '', process.stderr
    last = json.loads((s.owner_root / 'caller-boundary.json').read_text())['cleanup']
    assert last['status'] == 'cleanup_pending' and last['work']['mode'] == 'execute'
    assert all(event['observation'] is None for event in last['history'])
    assert s.query() == last
    if position == 'before':
        # A well-shaped observation cannot grant permission outside reconcile.
        s.refuse(s.packet('observe', work=last['work'],
                          observation=DATA['owner_recovery']['not_started']))
        assert s.query() == last
    assert s.temporary.exists() is (position == 'before')
    expected_effects = {} if position == 'before' else {'remove:temporary': 1}
    assert s.owner_state()['effects'] == expected_effects
    resumed = s.proceed(disposition=discard)
    assert resumed['work']['mode'] == 'reconcile'
    assert resumed['work']['attempt_id'] == last['work']['attempt_id']
    assert resumed['work']['step_id'] == last['work']['step_id']
    assert 'receipt_id' not in resumed['work']
    observed = physical(s.owner_root, resumed['work'])
    if position == 'before':
        assert observed == DATA['owner_recovery']['not_started']
        executable = s.observe(resumed['work'], observed)
        assert executable['work']['mode'] == 'execute'
        assert executable['work']['attempt_id'] == resumed['work']['attempt_id']
        final_observation = physical(s.owner_root, executable['work'])
        done = s.observe(executable['work'], final_observation)
    else:
        assert observed['status'] == 'succeeded'
        assert observed['receipt_id'] == 'receipt:' + last['work']['attempt_id']
        done = s.observe(resumed['work'], observed)
    assert done['status'] == 'cleanup_complete' and done['resources'] == []
    assert done['removed'] == [s.resources[2]] and done['work'] is None
    assert s.owner_state()['effects'] == {'remove:temporary': 1}
    assert not s.temporary.exists() and s.query() == done
    assert s.owner_state()['calls'][-1]['attempt_id'] == last['work']['attempt_id']
    return s, done


def unknown_effect(root):
    # A valid public probe establishes RED before caller-loss subprocess checks.
    probe = CleanupSubject(root / 'capability')
    probe.prepare(resources=[], disposition=None)
    before, _ = lost_caller(root / 'before', 'before')
    mark('real_caller_loss_before_dispatch_without_any_observe')
    after, _ = lost_caller(root / 'after', 'after')
    mark('real_caller_loss_after_effect_without_any_observe')
    assert [value['mode'] for value in before.owner_state()['calls']] == ['reconcile', 'execute']
    assert [value['mode'] for value in after.owner_state()['calls']] == ['execute', 'reconcile']
    mark('same_attempt_owner_lookup_without_receipt_id')
    uncertain = CleanupSubject(root / 'uncertain')
    discard = {'kind': 'discard_authorized', 'expected_commit': None}
    cleanup = uncertain.prepare(resources=[uncertain.resources[2]], disposition=discard)
    uncertain.owner_setting(unknown=True)
    observed = physical(uncertain.owner_root, cleanup['work'])
    assert observed == DATA['owner_recovery']['unknown']
    pending = uncertain.observe(cleanup['work'], observed)
    assert pending['status'] == 'cleanup_pending' and pending['issue'] == observed['result']
    assert pending['work']['mode'] == 'reconcile'
    continued = uncertain.proceed(disposition=discard)
    assert continued['work']['attempt_id'] == cleanup['work']['attempt_id']
    assert continued['work']['mode'] == 'reconcile'
    assert uncertain.owner_state()['effects'] == {} and uncertain.temporary.exists()
    mark('not_started_reopens_same_attempt_and_unknown_stays_reconcile')
    assert before.owner_state()['effects'] == after.owner_state()['effects'] == {'remove:temporary': 1}
    assert before.query()['status'] == after.query()['status'] == 'cleanup_complete'
    mark('saved_completion_reconciles_with_one_effect')


def replay_history(root):
    s = CleanupSubject(root / 'replay')
    packet = s.packet('prepare', resources=s.resources[:2])
    result = s.write(packet)
    before = rows(s.database), s.owner_state(), s.effect_observation()
    code, replay = invoke(s.config, packet)
    assert code == 0 and replay == dict(result, replayed=True)
    assert (rows(s.database), s.owner_state(), s.effect_observation()) == before
    work = result['cleanup']['work']
    observed = physical(s.owner_root, work)
    observation_packet = s.packet('observe', work=work, observation=observed)
    observed_result = s.write(observation_packet)
    before = rows(s.database), s.owner_state(), s.effect_observation()
    code, replay = invoke(s.config, observation_packet)
    assert code == 0 and replay == dict(observed_result, replayed=True)
    assert (rows(s.database), s.owner_state(), s.effect_observation()) == before
    mark('exact_request_replay_and_duplicate_observation_have_no_new_record_or_effect')
    changed = s.packet('prepare', resources=s.resources[:2])
    changed['input']['authorization'] = 'Changed original cleanup intent.'
    s.refuse(changed)
    stale = s.packet('continue')
    stale['input']['expected_revision'] -= 1
    s.refuse(stale)
    mark('changed_intent_and_stale_revision_leave_rows_unchanged')
    cleanup = observed_result['cleanup']
    wrong = s.packet('observe', work=cleanup['work'], observation=observed)
    wrong['input']['step_id'] = 'different-step'
    s.refuse(wrong)
    s.owner_setting(fail_once='branch')
    old_work = cleanup['work']
    old_failure = physical(s.owner_root, old_work)
    cleanup = s.observe(old_work, old_failure)
    assert cleanup['status'] == 'cleanup_blocked'
    cleanup = s.proceed()
    assert cleanup['work']['attempt_id'] != old_work['attempt_id']
    s.refuse(s.packet('observe', work=old_work, observation=old_failure))
    mark('wrong_step_or_late_old_attempt_observation_refused')
    done, _ = s.drain(cleanup, [('remove', 'branch')])
    # Ordinary test-owned later-generation arrangement; cleanup is not the restart subject.
    with sqlite3.connect(s.database) as db:
        db.execute('UPDATE executions SET generation=? WHERE task_id=?', (2, '0042'))
    cfg = json.loads(s.config.read_text())
    del cfg['cleanup']
    write_json(s.config, cfg)
    assert s.query() == done
    before = rows(s.database), s.owner_state()
    code, replay = invoke(s.config, packet)
    assert code == 0 and replay == dict(result, replayed=True)
    assert (rows(s.database), s.owner_state()) == before
    assert s.query()['generation'] == 1
    mark('complete_history_read_after_process_reopen_and_later_generation')
    s.preserved_original_records()
    with sqlite3.connect(s.database) as db:
        rows_now = db.execute('SELECT data_json FROM execution_records WHERE kind=? '
                             'AND task_id=? ORDER BY seq', ('outcome_fact', '0042')).fetchall()
    assert len(rows_now) == 1 and json.loads(rows_now[0][0])['fact']['kind'] == 'cancelled'
    mark('outcome_and_noncleanup_execution_records_preserved')


def input_policy_rejections(root):
    probe = CleanupSubject(root / 'capability')
    probe.prepare(resources=[], disposition=None)
    for case in DATA['wire_cases']:
        variants = case.get('variants', [None])
        if case.get('special') == 'extra_workspace_pair':
            variants = ['worktree', 'branch']
        for index, recipe in enumerate(variants):
            s = CleanupSubject(root / (case['id'] + '-' + str(index)))
            if case.get('observe'):
                cleanup = s.prepare()
                observation = bind(DATA['owner_success']['remove'], {'@RECEIPT': 'test-owner-receipt'})
                packet = s.packet('observe', work=cleanup['work'], observation=observation)
            else:
                packet = s.packet('prepare')
            if case.get('special') == 'duplicate_resource_id':
                packet['input']['resources'][1]['resource_id'] = 'worktree'
            elif case.get('special') == 'extra_workspace_pair':
                extra = deepcopy(next(item for item in s.resources if item['kind'] == recipe))
                extra['resource_id'] += '-second'
                packet['input']['resources'].append(extra)
            else:
                packet = mutate(packet, recipe)
            s.refuse(packet, case['reason_any'])
        mark(case['id'])
    for case in DATA['policy_cases']:
        for index, recipe in enumerate(case['variants']):
            s = CleanupSubject(root / (case['id'] + '-' + str(index)))
            packet = s.packet('prepare')
            config = mutate(json.loads(s.config.read_text()), recipe)
            write_json(s.config, config)
            s.refuse(packet, case['reason_any'])
        mark(case['id'])


GROUPS = [terminal_decisions, dispositions, ordered_effects, failure_resume,
          unknown_effect, replay_history, input_policy_rejections]


def main():
    assert sys.argv[1:] in ([], ['--suite', 'behavior']), sys.argv
    directory = Path(os.environ.get('POISE_RUN_OUTPUT_DIR')
                     or tempfile.mkdtemp(prefix='execution-cleanup-'))
    directory.mkdir(parents=True, exist_ok=True)
    cases, missing, errors = [], [], []
    for (name, expected), check in zip(DATA['groups'].items(), GROUPS, strict=True):
        start = len(LOG)
        try:
            check(directory / name)
            actual = [row['subcheck'] for row in LOG[start:] if 'subcheck' in row]
            assert len(actual) == len(set(actual)) and set(actual) == set(expected), (actual, expected)
            cases.append({'group': name, 'status': 'passed', 'subchecks': actual})
        except MissingCleanup:
            missing.append(name)
            cases.append({'group': name, 'status': 'missing-interface', 'response': DATA['missing']})
        except Exception:
            failure = {'group': name, 'traceback': traceback.format_exc()}
            errors.append(failure)
            cases.append(dict(failure, status='failed'))
    intended = missing == list(DATA['groups']) and not errors
    status = 'passed' if not missing and not errors else 'expected-missing-interface' if intended else 'failed'
    report = {'status': status, 'cases': cases, 'missing': missing,
              'errors': errors, 'intended_red': intended, 'logs': LOG}
    write_json(directory / 'cleanup-details.json', report)
    print(json.dumps({'status': status, 'groups': len(cases), 'missing': len(missing),
                      'errors': len(errors), 'intended_red': intended}, ensure_ascii=False))
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
