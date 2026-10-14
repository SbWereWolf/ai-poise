"""Public replanning with retained terminal proof and independent SQL oracles."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request, text_artifact
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import bootstrap
from sprints.test_unpublish import (
    client, git_state, handoff, intent, invoke, planned, prepare_member,
    reopen, sprint_rows, task_rows, unchanged,
)


def terminal_receipt():
    return json.loads((Path(__file__).parent / 'fixtures/terminal-integration.json').read_text())


def prepared_receipt(planner, mode='ordinary'):
    """Bind fixture inputs to observed accepted work, not a product-generated oracle."""
    with planner.runtime.store.transaction() as db:
        row = db.execute('SELECT data FROM task_execution WHERE task_id=?', ('A',)).fetchone()
    observed = json.loads(row[0])
    source = observed['last_report']['commit']
    receipt = terminal_receipt()
    receipt['intent']['expected_source_commit'] = source
    receipt['accepted_commit'] = source
    receipt['task_branch'] = observed['branch']
    receipt['task_worktree'] = observed['worktree']
    publication = receipt['publication']
    if mode == 'no-op':
        head = receipt['last_included_target']
        receipt.update(integration_head=head, target_after=head)
        publication.update(commit=head, no_op=True)
    if mode in ('no-op', 'recovered'):
        publication['recovered'] = True
        publication['receipt'] = {
            'kind': 'reconciled_fast_forward', 'commit': receipt['integration_head'],
        }
    return receipt


def set_execution(planner, task_id, pending, publication=None):
    """Arrange only fixture data; no production serializer generates the receipt."""
    with planner.runtime.store.transaction() as db:
        row = db.execute('SELECT data,version FROM task_execution WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            data = {
                'worktree': None, 'branch': None, 'base': None, 'attempts': 0,
                'publication': publication, 'pending': pending,
                'entry_tree': None, 'last_report': None,
            }
            db.execute('INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)',
                       (task_id, json.dumps(data), 0))
        else:
            data = json.loads(row[0])
            data.update(pending=pending, publication=publication)
            db.execute('UPDATE task_execution SET data=?,version=? WHERE task_id=? AND version=?',
                       (json.dumps(data), row[1] + 1, task_id, row[1]))


def audit_rows(planner):
    with planner.runtime.store.transaction() as db:
        return {
            'journal': [tuple(row) for row in db.execute(
                "SELECT seq,session_id,task_id,event,data FROM journal WHERE event='sprint.changed' ORDER BY seq")],
            'bindings': [tuple(row) for row in db.execute(
                'SELECT session_id,sprint_id FROM session_sprints ORDER BY session_id')],
        }


def snapshot(planner):
    return unchanged(planner), audit_rows(planner)


def assert_refused_unchanged(planner, revision, diagnostic='pending|external|publication|invalid|intent|commit|source|receipt'):
    before = snapshot(planner)
    with pytest.raises(PoiseError, match=diagnostic):
        reopen(planner, revision)
    assert snapshot(planner) == before


@pytest.mark.parametrize('mode', ['ordinary', 'no-op', 'recovered'])
def test_terminal_admission_preserves_results_materials_and_replay(planned, mode):
    project, planner, published = planned
    prepare_member(project, 'completed')
    receipt = prepared_receipt(planner, mode)
    set_execution(planner, 'A', receipt)

    # B owns retained work and evidence; A's delivery may legitimately remove A's tree.
    worker = client(project, 'retained-worker')
    context = bootstrap(worker, 'B')
    payload = deepcopy(context['result_template'])
    payload['sections']['report'] = 'Retained participant evidence.'
    payload['commit_message'] = 'Preserve participant evidence'
    verified = worker.invoke(request('verify', {'result': payload, 'artifacts': [
        text_artifact('task', 'retained-proof.txt', 'independent retained artifact\n'),
    ]}))
    released = handoff(worker, 'retained-B')
    outputs = [Path(released['receipt_path'])]
    for check in verified['checks']:
        outputs.extend([Path(check['stdout']), Path(check['stderr'])])
    outputs.extend(Path(item['path']) for item in verified['artifacts'])
    assert len(verified['checks']) == 1
    assert len(verified['artifacts']) == 1
    assert Path(verified['artifacts'][0]['path']).read_bytes() == b'independent retained artifact\n'
    proof = {path: path.read_bytes() for path in outputs}
    root = Path(context['worktree'])
    wip = root / 'src/retained-terminal-wip.txt'
    wip.write_bytes(b'retained staged work\n')
    subprocess.run(['git', '-C', str(root), 'add', '--', 'src/retained-terminal-wip.txt'], check=True)
    wip.write_bytes(b'retained participant work\x00\xff\n')
    wip.chmod(0o640)
    material = wip.read_bytes(), wip.stat().st_mode, git_state(root)
    before_tasks = task_rows(planner)
    before_sprint = sprint_rows(planner)
    before_audit = audit_rows(planner)
    packet = intent(published['revision'])

    try:
        result = invoke(planner, packet)
    except PoiseError as exc:
        if str(exc) == 'Member has an unresolved pending external publication: A':
            print('EXPECTED_RED: completed terminal integration rejected')
            pytest.fail('A confirmed terminal integration must allow replanning.', pytrace=False)
        raise

    assert result['sprint'] == 'S'
    assert result['status'] == 'draft'
    assert result['revision'] == published['revision'] + 1
    assert result['dependencies'] == [{'predecessor': 'A', 'successor': 'B', 'kind': 'completion'}]
    assert task_rows(planner) == before_tasks
    after_sprint = sprint_rows(planner)
    assert len(after_sprint['layers']) == len(before_sprint['layers']) + 1
    assert len(after_sprint['requests']) == len(before_sprint['requests']) + 1
    assert after_sprint['layers'][:-1] == before_sprint['layers']
    assert set(before_sprint['requests']).issubset(set(after_sprint['requests']))
    assert after_sprint['dependencies'] == before_sprint['dependencies']
    aggregate = json.loads(after_sprint['sprints'][0][3])['aggregate']
    decision = aggregate['decisions'][-1]
    assert decision == {'kind': 'unpublish', 'reason': packet['reason'], 'authorization': packet['authorization']}
    assert len(audit_rows(planner)['journal']) == len(before_audit['journal']) + 1
    assert audit_rows(planner)['bindings'] == before_audit['bindings']
    assert {path: path.read_bytes() for path in proof} == proof
    assert (wip.read_bytes(), wip.stat().st_mode, git_state(root)) == material
    after = snapshot(planner)
    assert invoke(planner, packet) == result
    assert snapshot(planner) == after
    with pytest.raises(PoiseError, match='Request|request'):
        invoke(planner, {**packet, 'reason': 'Changed request.'})
    assert snapshot(planner) == after
    with pytest.raises(PoiseError, match='revision'):
        reopen(planner, published['revision'], request_id='stale-terminal-reopen')
    assert snapshot(planner) == after


@pytest.mark.parametrize('path,value', [
    (('intent', 'task_id'), 'B'),
    (('status',), 'cleanup_pending'),
    (('phase',), 'cleanup_pending'),
    (('cleanup', 'task_worktree'), 'pending'),
    (('cleanup', 'task_branch'), 'blocked'),
    (('cleanup', 'temporary_backups'), 'pending'),
    (('publication',), None),
    (('publication', 'status'), 'pending'),
    (('publication', 'commit'), '4444444444444444444444444444444444444444'),
    (('publication', 'last_included_target'), None),
    (('integration_head',), None),
    (('target_after',), '4444444444444444444444444444444444444444'),
    (('accepted_commit',), '4444444444444444444444444444444444444444'),
])
def test_incomplete_or_contradictory_receipt_refuses_atomically(planned, path, value):
    project, planner, published = planned
    prepare_member(project, 'completed')
    receipt = prepared_receipt(planner)
    target = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    set_execution(planner, 'A', receipt)
    assert_refused_unchanged(planner, published['revision'])


@pytest.mark.parametrize('case', [
    'missing-intent', 'missing-message', 'unsupported-schema', 'publication-list',
    'cleanup-string', 'cleanup-short-pairs', 'history-null', 'checks-null',
    'equal-null-commits', 'equal-untyped-commits', 'absent-target-provenance',
])
def test_malformed_or_unsupported_receipt_refuses_atomically(planned, case):
    project, planner, published = planned
    prepare_member(project, 'completed')
    receipt = prepared_receipt(planner)
    if case == 'missing-intent':
        del receipt['intent']
    elif case == 'missing-message':
        del receipt['intent']['commit_message']
    elif case == 'unsupported-schema':
        receipt['schema'] = 'unsupported'
    elif case == 'publication-list':
        receipt['publication'] = []
    elif case == 'cleanup-string':
        receipt['cleanup'] = 'invalid'
    elif case == 'cleanup-short-pairs':
        receipt['cleanup'] = [['task_worktree']]
    elif case == 'history-null':
        receipt['history'] = None
    elif case == 'checks-null':
        receipt['checks'] = None
    elif case == 'absent-target-provenance':
        receipt['last_included_target'] = None
        receipt['publication']['last_included_target'] = None
    else:
        value = None if case == 'equal-null-commits' else True
        receipt.update(integration_head=value, target_after=value)
        receipt['publication']['commit'] = value
    set_execution(planner, 'A', receipt)
    assert_refused_unchanged(planner, published['revision'])


@pytest.mark.parametrize('case', ['nonterminal', 'publication', 'task-claim', 'tree-claim', 'other-pending'])
def test_terminal_proof_preserves_other_admission_guards(planned, case):
    project, planner, published = planned
    if case != 'nonterminal':
        prepare_member(project, 'completed')
    receipt = terminal_receipt() if case == 'nonterminal' else prepared_receipt(planner)
    publication = {'kind': 'unknown-effect'} if case == 'publication' else None
    set_execution(planner, 'A', receipt, publication)
    if case in ('task-claim', 'tree-claim'):
        with planner.runtime.store.transaction() as db:
            if case == 'task-claim':
                db.execute('UPDATE tasks SET claimed_by=?,version=version+1 WHERE id=?', ('fixture-holder', 'A'))
            else:
                db.execute('INSERT INTO sessions(id,task_id) VALUES(?,?)', ('fixture-holder', 'A'))
    elif case == 'other-pending':
        set_execution(planner, 'B', {'kind': 'unknown-effect'})
    assert_refused_unchanged(planner, published['revision'], 'pending|external|publication|owned|claim')


@pytest.mark.parametrize('case', [
    'wrong-source-pair', 'no-op-with-different-target',
    'ordinary-with-equal-target', 'recovered-with-ordinary-receipt',
])
def test_contradictory_source_and_publication_provenance_refuses(planned, case):
    project, planner, published = planned
    prepare_member(project, 'completed')
    mode = 'no-op' if case == 'ordinary-with-equal-target' else 'ordinary'
    if case == 'no-op-with-different-target':
        mode = 'recovered'
    receipt = prepared_receipt(planner, mode)
    if case == 'wrong-source-pair':
        wrong = '1111111111111111111111111111111111111111'
        assert receipt['accepted_commit'] != wrong
        receipt['accepted_commit'] = wrong
        receipt['intent']['expected_source_commit'] = wrong
    elif case == 'no-op-with-different-target':
        receipt['publication']['no_op'] = True
    elif case == 'ordinary-with-equal-target':
        receipt['publication']['no_op'] = False
    else:
        receipt['publication']['recovered'] = True
    set_execution(planner, 'A', receipt)
    assert_refused_unchanged(planner, published['revision'])
