"""Public replanning with retained terminal proof and independent SQL oracles."""
from copy import deepcopy
import json
from pathlib import Path
import re
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


def prepared_receipt(planner, project, mode='ordinary'):
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
    # Bind declared fixture paths, not the production backup-scope builder.
    state = project['root'] / project['cfg']['paths']['state']
    assert state == planner.runtime.state
    scope = Path(receipt['temporary_backup_directory']).relative_to('/fixture/state/runtime')
    receipt['temporary_backup_directory'] = str(state / project['cfg']['paths']['runtime'] / scope)
    publication = receipt['publication']
    publication['receipt']['argv'][2] = str(project['app'])
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
    receipt = prepared_receipt(planner, project, mode)
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
    receipt = prepared_receipt(planner, project)
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
    receipt = prepared_receipt(planner, project)
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
    prepare_member(project, 'verified' if case == 'nonterminal' else 'completed')
    receipt = prepared_receipt(planner, project)
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
    receipt = prepared_receipt(planner, project, mode)
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


@pytest.mark.parametrize('case', [
    'foreign-target', 'missing-repository-prefix', 'wrong-repository',
    'unrelated-command', 'missing-stream', 'self-consistent-foreign-branch',
])
def test_foreign_target_or_publication_action_refuses_atomically(planned, case):
    project, planner, published = planned
    prepare_member(project, 'completed')
    receipt = prepared_receipt(planner, project)
    publication = receipt['publication']
    command = publication['receipt']
    if case == 'foreign-target':
        publication['target_ref'] = 'refs/heads/master'
    elif case == 'missing-repository-prefix':
        command['argv'] = ['git', 'merge', '--ff-only', 'tasks/A']
    elif case == 'wrong-repository':
        command['argv'][2] = str(project['root'] / 'foreign-repository')
    elif case == 'unrelated-command':
        command['argv'] = ['git', '-C', str(project['app']), 'status']
    elif case == 'missing-stream':
        del command['stderr']
    else:
        receipt['task_branch'] = 'tasks/B'
        command['argv'][-1] = 'tasks/B'
    set_execution(planner, 'A', receipt)
    assert_refused_unchanged(planner, published['revision'])


@pytest.mark.parametrize('case', [
    'foreign-backup-scope',
    'missing-report-commit',
    'missing-execution-branch',
    'nonobject-last-report',
    'execution-json-array',
    'execution-invalid-json',
])
def test_damaged_terminal_facts_refuse_atomically(planned, case):
    project, planner, published = planned
    prepare_member(project, 'completed')
    set_execution(planner, 'A', prepared_receipt(planner, project))
    with planner.runtime.store.transaction() as db:
        row = db.execute('SELECT data,version FROM task_execution WHERE task_id=?', ('A',)).fetchone()
        metadata = db.execute('SELECT metadata FROM tasks WHERE id=?', ('A',)).fetchone()[0]
        data = json.loads(row[0])
        if case == 'foreign-backup-scope':
            data['pending']['temporary_backup_directory'] = '/fixture/foreign-backups'
        elif case == 'missing-report-commit':
            del data['last_report']['commit']
        elif case == 'missing-execution-branch':
            del data['branch']
        elif case == 'nonobject-last-report':
            data['last_report'] = 'damaged-report'
        raw = {'execution-json-array': '[]', 'execution-invalid-json': '{'}.get(case)
        if raw is None:
            raw = json.dumps(data)
        changed = db.execute(
            'UPDATE task_execution SET data=?,version=? WHERE task_id=? AND version=?',
            (raw, row[1] + 1, 'A', row[1]),
        )
        assert changed.rowcount == 1
        assert db.execute('SELECT data FROM task_execution WHERE task_id=?', ('A',)).fetchone()[0] == raw
        assert db.execute('SELECT metadata FROM tasks WHERE id=?', ('A',)).fetchone()[0] == metadata
    before = snapshot(planner)
    try:
        reopen(planner, published['revision'])
    except PoiseError as exc:
        assert re.search(r'\bA\b', str(exc))
        assert re.search('facts|execution|report|branch|result|data|pending|publication|scope|invalid',
                         str(exc), re.IGNORECASE)
        assert snapshot(planner) == before
        return
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        expected = (
            (case == 'missing-report-commit' and type(exc) is KeyError and exc.args == ('commit',))
            or (case == 'missing-execution-branch' and type(exc) is KeyError and exc.args == ('branch',))
            or (case == 'nonobject-last-report' and type(exc) is TypeError
                and str(exc).startswith('string indices must be integers'))
            or (case == 'execution-json-array' and type(exc) is TypeError
                and str(exc).startswith('list indices must be integers'))
            or (case == 'execution-invalid-json' and type(exc) is json.JSONDecodeError
                and exc.doc == '{' and exc.pos == 1)
        )
        if not expected:
            raise
        assert snapshot(planner) == before
        print(f'EXPECTED_RED: {case}')
        pytest.fail('An internal exception is not an explained public refusal.', pytrace=False)
    if case != 'foreign-backup-scope':
        pytest.fail('Damaged execution data was unexpectedly admitted.', pytrace=False)
    print(f'EXPECTED_RED: {case}')
    pytest.fail('A foreign backup scope must refuse replanning.', pytrace=False)
