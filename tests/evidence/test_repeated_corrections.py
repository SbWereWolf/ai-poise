"""New correction versus replay: real public fixture roles, independent literals."""
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from tests.evidence.file_proof_support import Scenario, fixture, require


class MachineWitness:
    def __init__(self, scenario, response, expected):
        require(len(response['checks']) == 1, 'One actual machine guard must run')
        receipt = response['checks'][0]
        require(receipt['method'] == 'REAL-RECEIPT' and receipt['passed'] is True
                and receipt['actual_exit_code'] == 0 and receipt['capture_complete'] is True,
                'The real machine control did not complete successfully')
        self.records = deepcopy(scenario.runtime.evidence_commands.list_for('T1'))
        require(len(self.records) == 1 and self.records[0]['id'] == receipt['id'],
                'Machine control must have its genuine persisted receipt')
        self.streams = {receipt[name]: expected['machine_' + name].encode('utf-8')
                        for name in ('stdout', 'stderr')}
        self.assert_preserved(scenario)

    def assert_preserved(self, scenario):
        assert scenario.runtime.evidence_commands.list_for('T1') == self.records
        for path, original in self.streams.items():
            assert Path(path).read_bytes() == original


def proof(view, identifier):
    return next(a for a in view['arguments'] if a['id'] == identifier)


def accepted_pair(project, expected):
    s = Scenario(project, first='B', machine=True)
    response = s.register('B', 'A')
    machine = MachineWitness(s, response, expected)
    s.transfer()
    require(s.context['stage'] == 'audit', 'Expected actual initial audit entry')
    review = s.submit(decisions=[s.decide('B'), s.decide('A')])
    require(review['stage_outcome'] == 'clear', 'Initial proofs were not actually accepted')
    s.transfer()
    require(s.context['stage'] == 'refine' and s.context['iteration'] == 1,
            'Expected actual initial author refinement')
    initial = s.show()
    require(all(proof(initial, name)['state'] == 'accepted' for name in ('A', 'B')),
            'A and B must be accepted by real decisions')
    machine.assert_preserved(s)
    return s, machine


def first_rejection(s):
    require(s.submit()['status'] == 'verified', 'Empty real refine did not finish')
    s.transfer()
    require(s.context['stage'] == 'audit2', 'Expected actual rejection inspection')
    rejected = s.submit(decisions=[s.decide('A', 'rejected'), s.decide('B')])
    require(rejected['stage_outcome'] == 'changes_requested', 'A was not really rejected')
    s.transfer()
    require(s.context['stage'] == 'refine' and s.context['iteration'] == 2,
            'Expected real refinement after rejection')
    require(proof(s.show(), 'A')['state'] == 'rejected', 'Missing genuine A rejection')


def source_tree(s):
    return subprocess.check_output(
        ['git', '-C', s.context['worktree'], 'rev-parse', 'HEAD^{tree}'], text=True).strip()


def assert_current_bytes(s, expected):
    actual = s.read()
    assert actual['status'] == 'read_only'
    assert actual['argument_id'] == 'A' and actual['path'] == str(s.path())
    assert actual['representation'] == 'text'
    assert actual['content'] == expected['second_text']
    assert actual['size_bytes'] == expected['second_size_bytes']


@pytest.mark.parametrize('kind', ['author_unreliable', 'reviewer_rejected'])
def test_new_correction_reopens_same_metadata(project, kind):
    expected = fixture('repeated-corrections.json')
    case = expected['cases'][kind]
    s, machine = accepted_pair(project, expected)
    if kind == 'reviewer_rejected':
        first_rejection(s)
    require(s.context['iteration'] == case['first_iteration'],
            'The first correction must use the intended real iteration')
    cause = deepcopy(expected['causes'][kind])
    first_argument = s.argument(correction=cause)
    s.write(data=expected['first_text'].encode('utf-8'))
    first = s.submit([first_argument], required_contract=True)
    require(first['status'] == 'verified', 'First real correction did not verify')
    first_proof = proof(s.show(), 'A')
    require(first_proof['state'] == 'presented', 'First correction must be presented')
    require(first_proof['iteration'] == case['first_iteration'],
            'First correction provenance is not current')
    require(first_proof['tree'] == source_tree(s), 'Source tree must be actual Git state')
    machine.assert_preserved(s)

    s.transfer()
    decisions = ([s.decide('A'), s.decide('B', 'rejected')]
                 if kind == 'author_unreliable' else [s.decide('A', 'rejected')])
    reviewed = s.submit(decisions=decisions)
    require(reviewed['stage_outcome'] == 'changes_requested',
            'A real inspection must authorize the next refinement cycle')
    s.transfer()
    require(s.context['stage'] == expected['stage']
            and s.context['iteration'] == case['second_iteration'],
            'The second correction needs its genuine next refinement context')
    before = deepcopy(s.show())
    require(proof(before, 'A')['state'] == case['prior_second_state'],
            'Second correction prior acceptance/rejection is not genuine')
    old_b = deepcopy(proof(before, 'B'))
    require(s.path('B').read_bytes() == expected['b_text'].encode('utf-8'),
            'B byte sentinel is missing')
    second_argument = s.argument(correction=deepcopy(cause))
    require(second_argument == first_argument, 'All eight input fields must really repeat')
    unchanged_b = s.argument('B')
    require(all(old_b[field] == value for field, value in unchanged_b.items()),
            'B is the identical null-correction control')
    require(unchanged_b['correction'] is None, 'B must not become another correction')
    s.write(data=expected['second_text'].encode('utf-8'))
    inputs = [second_argument, unchanged_b]
    second = s.submit(inputs, required_contract=True)
    assert second['status'] == 'verified'
    after = s.show()
    actual = proof(after, 'A')
    assert_current_bytes(s, expected)
    assert proof(after, 'B') == old_b
    assert s.path('B').read_bytes() == expected['b_text'].encode('utf-8')
    assert after['decisions'] == before['decisions']
    assert all(actual[field] == value for field, value in second_argument.items())
    machine.assert_preserved(s)
    # Earliest falsifying predicate: the previous state cannot certify this correction.
    assert actual['state'] == expected['expected_state'], (
        f"New {kind} correction inherited {actual['state']} instead of presented")
    assert actual['stage'] == expected['stage']
    assert actual['iteration'] == case['second_iteration']
    assert actual['tree'] == source_tree(s)

    snapshot, view = s.business(), deepcopy(s.show())
    repeated = s.submit(inputs, required_contract=True)
    assert repeated['replayed'] is True
    assert s.business() == snapshot and s.show() == view
    machine.assert_preserved(s)
    s.transfer()
    assert_current_bytes(s, expected)
    reviewed = s.submit(decisions=[s.decide('A')])
    assert reviewed['stage_outcome'] == 'clear'
    final = s.show()
    assert proof(final, 'A')['state'] == 'accepted'
    assert proof(final, 'B') == old_b
    assert [d['decision'] for d in final['decisions']
            if d['argument_id'] == 'A'] == case['a_decisions']
    snapshot, view = s.business(), deepcopy(final)
    repeated = s.submit(decisions=[s.decide('A')])
    assert repeated['replayed'] is True
    assert s.business() == snapshot and s.show() == view
    machine.assert_preserved(s)


def test_old_rejection_reason_cannot_authorize_new_correction_without_rejection(project):
    expected = fixture('repeated-corrections.json')
    s, machine = accepted_pair(project, expected)
    first_rejection(s)
    cause = deepcopy(expected['causes']['reviewer_rejected'])
    original = s.argument(correction=cause)
    s.write(data=expected['first_text'].encode('utf-8'))
    require(s.submit([original], required_contract=True)['status'] == 'verified',
            'First permitted rejected-proof correction failed')
    require(proof(s.show(), 'A')['state'] == 'presented', 'First correction was not presented')
    s.transfer()
    reviewed = s.submit(decisions=[s.decide('A'), s.decide('B', 'rejected')])
    require(reviewed['stage_outcome'] == 'changes_requested',
            'Actual B rejection must supply the next author entry')
    s.transfer()
    before = deepcopy(s.show())
    require(s.context['stage'] == expected['stage']
            and s.context['iteration'] == expected['negative']['iteration'],
            'Expected genuine next refinement without a new A rejection')
    require(proof(before, 'A')['state'] == expected['negative']['before_state'],
            'A must really be accepted before this invalid rejection claim')
    argument = s.argument(correction=deepcopy(cause))
    require(argument == original, 'Old eight-field rejection metadata must repeat exactly')
    s.write(data=expected['second_text'].encode('utf-8'))
    machine.assert_preserved(s)
    s.refused(lambda: s.submit([argument]), expected['negative']['error_prefix'])
    assert s.show() == before
    assert s.path('B').read_bytes() == expected['b_text'].encode('utf-8')
    machine.assert_preserved(s)
