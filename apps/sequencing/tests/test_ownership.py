"""Atomic ownership through real CLI processes and independent saved-state assertions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from apps.sequencing.tests.test_registration import Client, current_state


ROOT = Path(__file__).resolve().parents[3]
DATA = json.loads((ROOT / 'apps/sequencing/fixtures/ownership-contract.json').read_text())
A, B, C, D, E, F = '0041', '0042', '0043', '0044', '0045', '0046'
OWNER, OTHER, OPERATOR = 'SESSION-A', 'SESSION-B', 'SESSION-OPERATOR'


class Work:
    def __init__(self, root, form='sprint', reference='statement'):
        self.client = Client(root)
        self.count = 0
        self.reference = 'requirements_' + reference + '_revision'
        config = json.loads(self.client.config.read_text())
        config['identifiers']['task']['pattern'] = '^[0-9]{4}$'
        self.client.config.write_text(json.dumps(config))
        operations = deepcopy(DATA['registration_operations_' + reference])
        members = [B, C, D, E, F] if form == 'independent' else [A, B, C, D, E, F]
        self.members = set(members)
        operations[-1]['members'] = members
        packet = self.packet(operations, 'seed', 0)
        expected = DATA['registered_' + reference]
        assert self.client.invoke(packet) == expected
        assert current_state(self.client.database)['version'] == 2
        self.workspace_expected = None

    def rows(self, sql, values=()):
        with closing(sqlite3.connect(self.client.database)) as db:
            return db.execute(sql, values).fetchall()

    def write(self, sql, values=()):
        with closing(sqlite3.connect(self.client.database)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute(sql, values)
            db.commit()
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []

    @property
    def revision(self):
        return self.rows("SELECT value FROM meta WHERE key='revision'")[0][0]

    def packet(self, operations, request=None, revision=None):
        self.count += 1
        return {'schema': 'stride-sequencing/v1', 'operation': 'apply', 'input': {
            'request_id': request or 'request-' + str(self.count),
            'expected_revision': self.revision if revision is None else revision,
            'operations': operations}}

    def snapshot(self):
        markers = {str(p.relative_to(self.client.root)): p.read_bytes()
                   for p in self.client.root.rglob('uncommitted.txt')}
        return current_state(self.client.database), markers

    def apply(self, operation, request=None):
        revision = self.revision
        before = self.snapshot()
        references = self.rows('SELECT task_id,requirements_snapshot_revision,'
                               'requirements_statement_revision,terminal_json FROM tasks ORDER BY task_id')
        packet = self.packet([operation], request)
        result = self.client.invoke(packet)
        assert set(result) == {'schema', 'status', 'revision', 'replayed', 'results'}
        assert {key: result[key] for key in ('schema', 'status', 'revision', 'replayed')} == {
            'schema': 'stride-sequencing/v1', 'status': 'applied',
            'revision': revision + 1, 'replayed': False}
        assert len(result['results']) == 1
        value = result['results'][0]
        assert value['id'] == operation['id'] and value['kind'] == operation['kind']
        after = self.snapshot()
        assert before[0]['schema'] == after[0]['schema'] and before[0]['version'] == after[0]['version'] == 2
        assert before[1] == after[1]
        changed = {'tasks', 'task_claims', 'requests', 'meta', 'sequencing_history'}
        for table, rows in before[0]['rows'].items():
            if table not in changed:
                assert after[0]['rows'][table] == rows, table
        assert self.rows('SELECT task_id,requirements_snapshot_revision,'
                         'requirements_statement_revision,terminal_json FROM tasks ORDER BY task_id') == references
        prior_requests = before[0]['rows']['requests']
        assert all(row in after[0]['rows']['requests'] for row in prior_requests)
        assert len(after[0]['rows']['requests']) == len(prior_requests) + 1
        for row in before[0]['rows']['task_claims']:
            if row[3] == 'released' or row[1] not in (operation.get('task_id'), operation.get('from_task_id')):
                assert row in after[0]['rows']['task_claims']
        old_history = before[0]['rows']['sequencing_history']
        assert all(row in after[0]['rows']['sequencing_history'] for row in old_history)
        assert [row for row in before[0]['rows']['meta'] if row[0] != 'revision'] == [
            row for row in after[0]['rows']['meta'] if row[0] != 'revision']
        return value, packet, result

    def reject(self, operation, reason=None, packet=None):
        before = self.snapshot()
        response = self.client.invoke(packet or self.packet([operation]), exit_code=2)
        assert set(response) == {'schema', 'status', 'reason'}
        assert response['schema'] == 'stride-sequencing/v1'
        assert response['status'] == 'rejected'
        assert isinstance(response['reason'], str) and response['reason'].strip()
        if reason is not None:
            assert response['reason'] == reason
        assert self.snapshot() == before
        return response

    def claim_op(self, task=A, owner=OWNER, intent=None):
        return {**DATA['operations']['claim_task'], 'task_id': task, 'claimant': owner,
                'claim_request_id': intent or 'claim-' + task + '-' + owner}

    def claim(self, task=A, owner=OWNER, intent=None):
        value, packet, response = self.apply(self.claim_op(task, owner, intent))
        self.claim_value(value, task, owner)
        return value['claim_id'], packet, response

    @staticmethod
    def claim_value(value, task, owner, extra=None, replay=False):
        expected = {'id', 'kind', 'changed', 'task_id', 'claim_id', 'claimant', 'claim_replayed'}
        assert set(value) == expected | set(extra or ())
        assert value['task_id'] == task and value['claimant'] == owner
        assert value['changed'] is (not replay) and value['claim_replayed'] is replay
        assert isinstance(value['claim_id'], str) and value['claim_id'].strip()

    def release_op(self, claim, task=A, owner=OWNER):
        return {**DATA['operations']['release_claim'], 'task_id': task,
                'claim_id': claim, 'claimant': owner}

    def release(self, claim, task=A, owner=OWNER):
        value, packet, response = self.apply(self.release_op(claim, task, owner))
        self.release_value(value, claim, task)
        return value['handoff_id'], packet, response

    @staticmethod
    def release_value(value, claim, task):
        assert set(value) == {'id', 'kind', 'changed', 'task_id', 'claim_id', 'handoff_id'}
        assert value['changed'] is True and value['task_id'] == task and value['claim_id'] == claim
        assert isinstance(value['handoff_id'], str) and value['handoff_id'].strip()

    def resume_op(self, handoff, task=A, owner=OTHER):
        return {**DATA['operations']['resume_claim'], 'task_id': task,
                'claimant': owner, 'handoff_id': handoff}

    def resume(self, handoff, task=A, owner=OTHER):
        value, packet, response = self.apply(self.resume_op(handoff, task, owner))
        self.claim_value(value, task, owner)
        return value['claim_id'], packet, response

    def switch_op(self, claim, target=B, owner=OWNER, intent='switch-A-B'):
        return {**DATA['operations']['switch_claim'], 'from_claim_id': claim,
                'task_id': target, 'claimant': owner, 'claim_request_id': intent}

    def switch(self, claim):
        value, packet, response = self.apply(self.switch_op(claim))
        self.claim_value(value, B, OWNER, ('previous_task_id', 'previous_claim_id', 'handoff_id'))
        assert value['previous_task_id'] == A and value['previous_claim_id'] == claim
        assert isinstance(value['handoff_id'], str) and value['handoff_id']
        assert value['claim_id'] != claim
        return value, packet, response

    def recover_op(self, claim):
        return {**DATA['operations']['recover_claim'], 'claim_id': claim}

    def active(self, expected):
        assert self.rows('SELECT task_id,claimant,claim_id FROM task_claims '
                         "WHERE status='active' ORDER BY task_id") == sorted(expected)
        assert self.rows('SELECT task_id,count(*) FROM task_claims '
                         "WHERE status='active' GROUP BY task_id HAVING count(*)>1") == []
        assert self.rows('SELECT claimant,count(*) FROM task_claims '
                         "WHERE status='active' GROUP BY claimant HAVING count(*)>1") == []

    def task(self, task=A):
        packet = deepcopy(DATA['query_task'])
        packet['input']['queries'][0]['task_id'] = task
        response = self.client.invoke(packet)
        assert set(response) == {'schema', 'status', 'revision', 'results'}
        assert response['status'] == 'read_only' and response['revision'] == self.revision
        assert response['schema'] == 'stride-sequencing/v1'
        assert len(response['results']) == 1 and response['results'][0]['id'] == 'task'
        return response['results'][0]['value']

    def assert_task(self, task, state, claim=None):
        expected = {'task_id': task, self.reference: 1, 'state': state,
                    'sprint_id': 'SPR-OWN' if task in self.members else None,
                    'claim': claim, 'workspace': self.workspace_expected if task == A else None,
                    'terminal': None}
        assert self.task(task) == expected

    def availability(self, task, owner):
        packet = deepcopy(DATA['query_eligibility'])
        packet['input']['queries'][0].update(task_id=task, claimant=owner)
        response = self.client.invoke(packet)
        return response['results'][0]['value']

    def history(self):
        return self.rows('SELECT seq,task_id,sprint_id,workspace_id,actor,event,data,at '
                         'FROM sequencing_history ORDER BY seq')

    def audited(self, before, actor, identities):
        after = self.history()
        assert after[:len(before)] == before and len(after) > len(before)
        tail = after[len(before):]
        assert all(row[4] == actor and isinstance(row[5], str) and row[5] for row in tail)
        payloads = [json.loads(row[6]) for row in tail]
        flattened = json.dumps(payloads, ensure_ascii=False)
        assert all(identity in flattened or any(identity in row[:4] for row in tail)
                   for identity in identities)
        return payloads

    def handoff(self, handoff_id, claim_id):
        def dictionaries(value):
            if isinstance(value, dict):
                yield value
                for item in value.values():
                    yield from dictionaries(item)
            elif isinstance(value, list):
                for item in value:
                    yield from dictionaries(item)
        matches = [item for row in self.history() for item in dictionaries(json.loads(row[6]))
                   if item.get('handoff_id') == handoff_id and 'previous_claim_id' in item]
        expected = {'handoff_id': handoff_id, 'task_id': A, 'previous_claim_id': claim_id,
                    self.reference: 1,
                    'workspace_id': None if self.workspace_expected is None else 'WORKSPACE-A'}
        assert matches and all(item == expected for item in matches)

    def workspace(self, binding):
        path = self.client.root / 'workspace-A'
        path.mkdir()
        (path / 'uncommitted.txt').write_bytes(b'Uncommitted task work\x00\xff')
        self.write('INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
                   'VALUES(?,?,?,?,?,?,?)', ('WORKSPACE-A', A, 'repository-A', 'a' * 40,
                                           'tasks/' + A, 'ready', 0))
        self.write('INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
                   ('WORKSPACE-A', 'code', 0, str(path)))
        if binding is not None:
            self.write('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)',
                       ('WORKSPACE-A', binding))
        self.workspace_expected = {'workspace_id': 'WORKSPACE-A', 'task_id': A,
            'repository_id': 'repository-A', 'base_commit': 'a' * 40, 'branch': 'tasks/' + A,
            'status': 'ready', 'read_only': False,
            'roots': [{'kind': 'code', 'position': 0, 'path': str(path)}],
            'path': str(path), 'owner_session_id': binding}
        return self.resource_state()

    def resource_state(self):
        tables = current_state(self.client.database)['rows']
        return {name: value for name, value in tables.items()
                if name.startswith('workspace')}, self.snapshot()[1]

    def block(self, target, kind):
        if kind in ('completed', 'cancelled'):
            terminal = {'terminal_id': 'terminal-' + target, 'task_id': target,
                        'kind': kind, 'result_commit': 'b' * 40 if kind == 'completed' else None}
            self.write('UPDATE tasks SET state=?,terminal_json=? WHERE task_id=?',
                       (kind, json.dumps(terminal), target))
        else:
            edge = 'result' if kind == 'missing-result' else 'completion'
            state = 'completed' if kind == 'missing-result' else (
                'cancelled' if kind == 'cancelled-predecessor' else 'available')
            self.write('UPDATE tasks SET state=? WHERE task_id=?', (state, C))
            self.write('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) '
                       'VALUES(?,?,?,?)', ('SPR-OWN', C, target, edge))
        assert self.availability(target, OWNER)['eligible'] is False


@pytest.mark.parametrize('form', ['independent', 'sprint'])
@pytest.mark.parametrize('reference', ['statement', 'snapshot'])
def test_C1_claim_exclusivity_and_current_views(tmp_path, form, reference):
    w = Work(tmp_path, form, reference)
    before = w.history()
    claim, _, _ = w.claim()
    w.active([(A, OWNER, claim)])
    w.assert_task(A, 'claimed', {'claim_id': claim, 'task_id': A,
                              'claimant': OWNER, 'status': 'active'})
    w.audited(before, OWNER, [A, claim])
    assert w.availability(A, OWNER) == {
        'task_id': A, 'eligible': True, 'blockers': [], 'result_provenance': []}
    assert w.availability(A, OTHER) == {'task_id': A, 'eligible': False,
        'blockers': [{'reason': 'claimed_by_other', 'claim_id': claim, 'claimant': OWNER}],
        'result_provenance': []}
    w.reject(w.claim_op(A, OTHER), 'claim_conflict')
    w.reject(w.claim_op(B, OWNER), 'session_claim_conflict')


@pytest.mark.parametrize('direction', ['independent-to-sprint', 'sprint-to-independent'])
@pytest.mark.parametrize('reference', ['statement', 'snapshot'])
def test_C2_switch_preserves_old_claim_then_changes_both(tmp_path, direction, reference):
    w = Work(tmp_path, 'independent', reference)
    if direction == 'sprint-to-independent':
        w.write('DELETE FROM sprint_members WHERE task_id=?', (B,))
        w.write('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)', ('SPR-OWN', A))
        w.members.remove(B)
        w.members.add(A)
    w.workspace(OWNER)
    resources = w.resource_state()
    claim, _, _ = w.claim()
    foreign, _, _ = w.claim(B, OTHER)
    w.reject(w.switch_op(claim), 'claim_conflict')
    w.active([(A, OWNER, claim), (B, OTHER, foreign)])
    w.release(foreign, B, OTHER)
    before = w.history()
    value, _, _ = w.switch(claim)
    w.active([(B, OWNER, value['claim_id'])])
    w.assert_task(A, 'available')
    w.assert_task(B, 'claimed', {'claim_id': value['claim_id'], 'task_id': B,
                              'claimant': OWNER, 'status': 'active'})
    assert w.rows('SELECT status FROM task_claims WHERE claim_id=?', (claim,)) == [('released',)]
    w.handoff(value['handoff_id'], claim)
    w.audited(before, OWNER, [A, B, claim, value['claim_id'], value['handoff_id']])
    assert w.resource_state() == resources
    assert w.availability(B, OWNER)['eligible'] is True
    assert w.availability(B, OTHER)['eligible'] is False


BLOCKS = ['pending-predecessor', 'cancelled-predecessor', 'missing-result', 'completed', 'cancelled']


@pytest.mark.parametrize('block', BLOCKS + ['later-operation'])
def test_C3_switch_refusals_rollback_every_effect(tmp_path, block):
    w = Work(tmp_path)
    w.workspace(OWNER)
    claim, _, _ = w.claim()
    if block == 'later-operation':
        packet = w.packet([w.switch_op(claim), {
            'id': 'later', 'kind': 'register_sprint', 'sprint_id': 'MISSING-MEMBER',
            'members': ['0099'], 'dependencies': []}])
        w.reject(None, 'dependency_conflict', packet)
    else:
        w.block(B, block)
        w.reject(w.switch_op(claim), 'not_eligible')
    w.active([(A, OWNER, claim)])


@pytest.mark.parametrize('axis', ['same-task', 'same-session'])
def test_C4_real_concurrent_cli_writers_preserve_both_exclusivities(tmp_path, axis):
    w = Work(tmp_path)
    barrier = Barrier(2)
    operations = [w.claim_op(A, OWNER, 'race-first'),
                  w.claim_op(A if axis == 'same-task' else B,
                             OTHER if axis == 'same-task' else OWNER, 'race-second')]
    packets = [w.packet([op], 'race-' + str(index), 1) for index, op in enumerate(operations)]
    before = w.history()

    def run(packet):
        barrier.wait(timeout=10)
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'apps/sequencing/bin/sequencing'),
            '--config', str(w.client.config)], input=json.dumps(packet) + '\n', cwd=w.client.root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=30)
        assert process.stderr == ''
        return process.returncode, json.loads(process.stdout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(run, packets))
    assert sorted(code for code, _ in replies) == [0, 2], replies
    winner = next(index for index, item in enumerate(replies) if item[0] == 0)
    loser = 1 - winner
    assert replies[loser][1] == {
        'schema': 'stride-sequencing/v1', 'status': 'rejected', 'reason': 'revision_conflict'}
    response = replies[winner][1]
    assert set(response) == {'schema', 'status', 'revision', 'replayed', 'results'}
    assert response['status'] == 'applied' and response['revision'] == 2
    assert response['schema'] == 'stride-sequencing/v1' and response['replayed'] is False
    assert len(response['results']) == 1
    value = response['results'][0]
    op = operations[winner]
    w.claim_value(value, op['task_id'], op['claimant'])
    w.active([(op['task_id'], op['claimant'], value['claim_id'])])
    assert w.rows('SELECT request_id FROM requests ORDER BY request_id') == [
        ('race-' + str(winner),), ('seed',)]
    w.audited(before, op['claimant'], [op['task_id'], value['claim_id']])
    w.reject(operations[loser], 'claim_conflict' if axis == 'same-task' else 'session_claim_conflict')


@pytest.mark.parametrize('reference', ['statement', 'snapshot'])
@pytest.mark.parametrize('binding', [None, OWNER, OTHER])
def test_C5_release_resume_handoff_authority_consumption_and_resources(tmp_path, reference, binding):
    w = Work(tmp_path, reference=reference)
    resources = w.workspace(binding)
    claim, _, _ = w.claim()
    before = w.history()
    handoff, _, _ = w.release(claim)
    w.active([])
    w.assert_task(A, 'available')
    w.handoff(handoff, claim)
    w.audited(before, OWNER, [A, claim, handoff])
    w.reject(w.release_op(claim), 'claim_owner_mismatch')
    w.reject(w.resume_op('UNKNOWN'), 'handoff_not_found')
    w.reject(w.resume_op(handoff, B), 'claim_owner_mismatch')
    occupied, _, _ = w.claim(B, OTHER)
    w.reject(w.resume_op(handoff), 'session_claim_conflict')
    w.release(occupied, B, OTHER)
    w.block(A, 'pending-predecessor')
    w.reject(w.resume_op(handoff), 'not_eligible')
    w.write('DELETE FROM sprint_dependencies WHERE successor=?', (A,))
    before = w.history()
    resumed, packet, response = w.resume(handoff)
    assert resumed != claim
    w.active([(A, OTHER, resumed)])
    w.assert_task(A, 'claimed', {'claim_id': resumed, 'task_id': A,
                              'claimant': OTHER, 'status': 'active'})
    w.audited(before, OTHER, [A, resumed, handoff])
    w.reject(w.resume_op(handoff, owner=OWNER), 'claim_owner_mismatch')
    w.release(resumed, A, OTHER)
    w.reject(w.resume_op(handoff), 'claim_owner_mismatch')
    state = w.snapshot()
    assert w.client.invoke(packet) == {**response, 'replayed': True}
    assert w.snapshot() == state
    assert w.resource_state() == resources


@pytest.mark.parametrize('kind', ['claim', 'switch'])
def test_C6_historical_inner_and_outer_replay_never_reactivate_claims(tmp_path, kind):
    w = Work(tmp_path)
    original, claim_packet, claim_response = w.claim()
    if kind == 'switch':
        value, packet, response = w.switch(original)
        saved = value
        old = value['claim_id']
        task = B
    else:
        packet, response = claim_packet, claim_response
        saved = response['results'][0]
        old, task = original, A
    handoff, _, _ = w.release(old, task)
    new, _, _ = w.resume(handoff, task, OTHER)
    prior_history = w.history()
    before = w.snapshot()
    assert w.client.invoke(packet) == {**response, 'replayed': True}
    assert w.snapshot() == before
    op = packet['input']['operations'][0]
    replay, _, _ = w.apply(op)
    assert replay == {**saved, 'changed': False, 'claim_replayed': True}
    w.active([(task, OTHER, new)])
    assert w.history() == prior_history
    assert w.rows('SELECT status FROM task_claims WHERE claim_id=?', (old,)) == [('released',)]


@pytest.mark.parametrize('reference', ['statement', 'snapshot'])
@pytest.mark.parametrize('binding', [None, OWNER, OTHER])
def test_C7_explicit_recovery_releases_only_stopped_writer_and_preserves_history(tmp_path, reference, binding):
    w = Work(tmp_path, reference=reference)
    resources = w.workspace(binding)
    claim, _, _ = w.claim()
    operation = w.recover_op(claim)
    for field, value in [('writers_stopped', False), ('decision', ''), ('claim_id', 'WRONG')]:
        invalid = {**operation, field: value}
        w.reject(invalid, 'claim_owner_mismatch' if field == 'claim_id' else None)
    before = w.history()
    value, packet, response = w.apply(operation)
    w.release_value(value, claim, A)
    handoff = value['handoff_id']
    w.active([])
    w.assert_task(A, 'available')
    w.handoff(handoff, claim)
    w.audited(before, OPERATOR, [A, claim, handoff, operation['decision'], operation['reason']])
    w.reject(w.release_op(claim), 'claim_owner_mismatch')
    occupied, _, _ = w.claim(B, OTHER)
    w.reject(w.resume_op(handoff), 'session_claim_conflict')
    w.release(occupied, B, OTHER)
    w.block(A, 'pending-predecessor')
    w.reject(w.resume_op(handoff), 'not_eligible')
    w.write('DELETE FROM sprint_dependencies WHERE successor=?', (A,))
    new, _, _ = w.resume(handoff)
    w.active([(A, OTHER, new)])
    state = w.snapshot()
    assert w.client.invoke(packet) == {**response, 'replayed': True}
    assert w.snapshot() == state
    w.reject(w.recover_op(claim), 'claim_owner_mismatch')
    assert w.resource_state() == resources


@pytest.mark.parametrize('kind', ['claim', 'switch'])
def test_C8_changed_inner_intent_is_rejected_without_effects(tmp_path, kind):
    w = Work(tmp_path)
    claim, packet, _ = w.claim()
    if kind == 'switch':
        _, packet, _ = w.switch(claim)
    operation = deepcopy(packet['input']['operations'][0])
    if kind == 'switch':
        operation['reason'] = 'Другая причина.'
    else:
        operation['task_id'] = B
    w.reject(operation, 'request_reuse_conflict')


@pytest.mark.parametrize('failure', ['same-task', 'same-session', 'missing-task'])
def test_C9_multiple_operations_rollback_all_ownership_and_receipts(tmp_path, failure):
    w = Work(tmp_path)
    first = w.claim_op(A)
    first['id'] = 'first'
    second = w.claim_op(A if failure == 'same-task' else B,
                        OTHER if failure == 'same-task' else OWNER, 'second-intent')
    second['id'] = 'second'
    reason = 'claim_conflict' if failure == 'same-task' else 'session_claim_conflict'
    if failure == 'missing-task':
        second['task_id'] = '0099'
        reason = 'task_not_found'
    w.reject(None, reason, w.packet([first, second]))
    w.active([])
    indexes = w.rows("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
    assert ('active_task_claim',) in indexes and ('active_session_claim',) in indexes


@pytest.mark.parametrize('block', BLOCKS)
def test_C10_fresh_claim_checks_each_availability_gate(tmp_path, block):
    w = Work(tmp_path)
    w.block(A, block)
    w.reject(w.claim_op(), 'not_eligible')
    w.active([])


@pytest.mark.parametrize('authority', ['wrong-claim', 'stale-claim', 'foreign-caller', 'same-task'])
def test_C11_fresh_switch_checks_exact_source_authority(tmp_path, authority):
    w = Work(tmp_path)
    w.workspace(OWNER)
    claim, _, _ = w.claim()
    current = claim
    operation = w.switch_op(claim, intent='fresh-authority-intent')
    reason = 'claim_owner_mismatch'
    if authority == 'wrong-claim':
        operation['from_claim_id'] = 'WRONG'
    elif authority == 'stale-claim':
        handoff, _, _ = w.release(claim)
        current, _, _ = w.resume(handoff, owner=OWNER)
        assert current != claim
    elif authority == 'foreign-caller':
        operation['claimant'] = OTHER
    else:
        operation['task_id'] = A
        reason = 'switch_target_conflict'
    w.reject(operation, reason)
    w.active([(A, OWNER, current)])
    w.assert_task(A, 'claimed', {'claim_id': current, 'task_id': A,
                              'claimant': OWNER, 'status': 'active'})
    w.assert_task(B, 'available')


KINDS = ['claim_task', 'switch_claim', 'release_claim', 'resume_claim', 'recover_claim']
ADMISSION = [(kind, variant) for kind in KINDS for variant in ('missing', 'extra')]
ADMISSION += [('claim_task', 'null-caller'), ('switch_claim', 'numeric-claim'),
              ('release_claim', 'null-reason'), ('resume_claim', 'null-handoff'),
              ('recover_claim', 'numeric-operator'), ('claim_task', 'task-policy'),
              ('recover_claim', 'string-stopped'), ('recover_claim', 'numeric-stopped'),
              ('recover_claim', 'null-stopped')]
ADMISSION += [('claim_task', 'numeric-caller'), ('resume_claim', 'numeric-caller'),
              ('claim_task', 'null-intent'), ('claim_task', 'empty-intent'),
              ('recover_claim', 'null-decision'), ('recover_claim', 'numeric-decision'),
              ('release_claim', 'empty-reason'), ('switch_claim', 'container-intent')]


@pytest.mark.parametrize('kind,variant', ADMISSION)
def test_C12_C13_new_operation_fields_have_exact_scalar_contract(tmp_path, kind, variant):
    w = Work(tmp_path)
    claim, handoff = None, None
    if kind != 'claim_task':
        claim, _, _ = w.claim()
        if kind == 'resume_claim':
            handoff, _, _ = w.release(claim)
    operations = {
        'claim_task': w.claim_op(), 'switch_claim': w.switch_op(claim),
        'release_claim': w.release_op(claim),
        'resume_claim': w.resume_op(handoff), 'recover_claim': w.recover_op(claim)}
    valid = deepcopy(operations[kind])
    operation = deepcopy(valid)
    if variant == 'missing':
        del operation['task_id']
    elif variant == 'extra':
        operation['unknown_field'] = 'unexpected'
    else:
        mutations = {'null-caller': ('claimant', None), 'numeric-caller': ('claimant', 17),
                     'numeric-claim': ('from_claim_id', 17), 'null-reason': ('reason', None),
                     'null-handoff': ('handoff_id', None), 'numeric-operator': ('operator', 17),
                     'task-policy': ('task_id', 'TASK-A'), 'string-stopped': ('writers_stopped', 'false'),
                     'numeric-stopped': ('writers_stopped', 1), 'null-stopped': ('writers_stopped', None),
                     'null-intent': ('claim_request_id', None), 'empty-intent': ('claim_request_id', ''),
                     'null-decision': ('decision', None), 'numeric-decision': ('decision', 17),
                     'empty-reason': ('reason', ''), 'container-intent': ('claim_request_id', [])}
        field, value = mutations[variant]
        operation[field] = value
    w.reject(operation)
    # Same valid state must permit the well-formed control, preventing refusal-only oracles.
    value, _, _ = w.apply(valid)
    if kind in ('claim_task', 'resume_claim', 'switch_claim'):
        target = B if kind == 'switch_claim' else A
        owner = OTHER if kind == 'resume_claim' else OWNER
        extra = ('previous_task_id', 'previous_claim_id', 'handoff_id') if kind == 'switch_claim' else None
        w.claim_value(value, target, owner, extra)
        w.active([(target, owner, value['claim_id'])])
    else:
        w.release_value(value, claim, A)
        w.active([])
