"""Owner termination is durable permission to archive, never a receipt or PASS."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository
from poise.modules.evidence.domain import completed_receipts
from runtime_services.test_durable_check_attempts import candidate, fail_once
from runtime_services.test_failed_check_rework import _scenario
from runtime_services.test_task_restart import process_contract, restart
from runtime_services.test_uncertain_check_transfer import _handoff, _pending


CONTRACT = json.loads((Path(__file__).parent / 'fixtures' /
                       'check_termination_contract.json').read_text())


def state(tools):
    task = deepcopy(tools.runtime.task_queries.record('T1'))
    return {
        'task': task,
        'claims': tools.runtime.ownership.snapshot(tools.runtime.session),
        'receipts': deepcopy(tools.runtime.evidence_commands.list_for('T1')),
        'registry': deepcopy(tools.runtime.task_queries.verification_registry('T1')),
        'head': git(Path(task['worktree']), 'rev-parse', 'HEAD'),
        'status': git(Path(task['worktree']), 'status', '--short'),
    }


def assert_proof(attempt, index=0, *, exit_code=0):
    assert type(attempt['version']) is int
    assert attempt['version'] == 2
    run = attempt['runs'][index]
    expected = {**CONTRACT['attempt'],
                **{field: attempt[field] for field in CONTRACT['context_fields']},
                'run_id': run['run_id'], 'tree': attempt['verified_tree'],
                'actual_exit_code': exit_code}
    assert run.get('termination') == expected
    for field in ('child_reaped', 'process_group_stopped', 'capture_complete'):
        assert type(run['termination'][field]) is bool
    assert type(run['termination']['iteration']) is int
    assert type(run['termination']['actual_exit_code']) is int
    return deepcopy(run['termination'])


def patch_pending(tools, attempt):
    # Fault injection into a disposable fixture only; never the working Task DB.
    with tools.runtime.task_commands.unit_of_work() as unit:
        unit.execution.patch('T1', {'pending': deepcopy(attempt)})


def test_termination_proof_is_durable_before_receipt_persistence(project, monkeypatch):
    project['task']['planning'] = deepcopy(CONTRACT['planning'])
    tools, payload, calls = candidate(project, monkeypatch)
    at_receipt = []

    def lose_receipt(*args, **kwargs):
        at_receipt.append(deepcopy(tools.runtime.current_task()['pending']))
        raise OSError('lost receipt after termination')

    monkeypatch.setattr(tools.runtime.evidence_commands, 'record_receipt', lose_receipt)
    with pytest.raises(OSError, match='lost receipt'):
        verify(tools, deepcopy(payload))
    pending = tools.runtime.current_task()['pending']
    assert_proof(pending)
    assert at_receipt == [pending]
    assert tools.runtime.evidence_commands.list_for('T1') == []
    before = state(tools)
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(tools, deepcopy(payload))
    assert state(tools) == before
    assert calls == [pending['runs'][0]['run_id']]


def test_termination_save_failure_leaves_uncertainty_without_rerun(project, monkeypatch):
    project['task']['planning'] = deepcopy(CONTRACT['planning'])
    tools, payload, calls = candidate(project, monkeypatch)
    original = SqliteExecutionRepository.save
    failed = []

    def fail_proof(self, task_id, data, version):
        pending = data.get('pending')
        if (not failed and isinstance(pending, dict)
                and any(run.get('termination') is not None for run in pending['runs'])):
            failed.append(True)
            original(self, task_id, data, version)
            raise OSError('termination transaction rollback')
        return original(self, task_id, data, version)

    monkeypatch.setattr(SqliteExecutionRepository, 'save', fail_proof)
    with pytest.raises(OSError, match='termination transaction rollback'):
        verify(tools, deepcopy(payload))
    pending = tools.runtime.current_task()['pending']
    assert pending['runs'][0]['started'] is True
    assert pending['runs'][0].get('termination') is None
    assert tools.runtime.evidence_commands.list_for('T1') == []
    before = state(tools)
    with pytest.raises(PoiseError, match='Unknown check outcome'):
        verify(tools, deepcopy(payload))
    with pytest.raises(PoiseError, match='termination|quiescence|proof'):
        restart(tools, 'T1', before['task']['version'], 'no-proof-restart',
                authorization={'role': 'user', 'decision':
                               'Fixture user authorizes archival, not fabricated termination.'})
    assert state(tools) == before
    assert calls == [pending['runs'][0]['run_id']]


@pytest.mark.parametrize('window', ['lost-start-ack', 'transport-loss'], ids=str)
def test_preproof_disappearance_cannot_be_archived(project, monkeypatch, window):
    project['task']['planning'] = deepcopy(CONTRACT['planning'])
    tools, payload, calls = candidate(project, monkeypatch)
    if window == 'lost-start-ack':
        original = tools.runtime.runner.start_check_run

        def disappeared(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('transport disappeared before proof')

        monkeypatch.setattr(tools.runtime.runner, 'start_check_run', disappeared)
    else:
        def disappeared(*args, **kwargs):
            # The start permission is real; simulate loss before owner result delivery.
            raise OSError('transport disappeared before proof')

        monkeypatch.setattr(tools.runtime.check_runner, 'run', disappeared)
    with pytest.raises(OSError, match='transport disappeared'):
        verify(tools, deepcopy(payload))
    pending = tools.runtime.current_task()['pending']
    assert pending['runs'][0]['started'] is True
    assert tools.runtime.check_runner.active_ids() == ()
    before = state(tools)
    with pytest.raises(PoiseError, match='termination|quiescence|proof'):
        restart(tools, 'T1', before['task']['version'], f'no-{window}-proof',
                authorization={'role': 'user', 'decision':
                               'Fixture user permits recovery only with genuine stopping proof.'})
    assert state(tools) == before
    assert calls == []


@pytest.mark.parametrize('version', [1, 999], ids=['legacy-v1', 'unsupported'])
def test_unwitnessed_protocol_refuses_distinct_reviewer_archive(project, monkeypatch, version):
    tools, _, original, calls = _pending(project, monkeypatch)
    legacy = deepcopy(original)
    legacy['version'] = version
    for run in legacy['runs']:
        run.pop('termination', None)
    patch_pending(tools, legacy)
    tools.runtime.ownership.release_task('T1')
    reviewer = WorkTools(WorkPoise(project['config_path'], 'legacy-proof-reviewer'))
    reviewer.runtime.bootstrap({'id': 'T1'})
    before = state(reviewer)
    with pytest.raises(PoiseError, match='legacy|unsupported|termination|protocol'):
        restart(reviewer, 'T1', before['task']['version'], f'archive-version-{version}',
                authorization={'role': 'reviewer', 'decision':
                               'Independent approval cannot create absent owner proof.'})
    assert state(reviewer) == before
    assert before['task']['pending'] == legacy
    assert calls == [original['runs'][0]['run_id']]


@pytest.mark.parametrize('damage', [
    'absent', 'actor', 'task_id', 'attempt_id', 'run_id', 'stage', 'iteration',
    'submission_digest', 'execution_key', 'tree', 'schema', 'extra',
    'child_reaped', 'process_group_stopped', 'capture_complete',
    'boolean-exit', 'missing-field',
    'integer-child', 'null-group', 'integer-capture', 'negative-iteration',
    'null-schema', 'string-exit', 'boolean-iteration',
], ids=str)
def test_damaged_termination_refuses_archive_without_mutation(project, monkeypatch, damage):
    tools, _, pending, calls = _pending(project, monkeypatch)
    assert_proof(pending)
    altered = deepcopy(pending)
    proof = altered['runs'][0]['termination']
    if damage == 'absent':
        altered['runs'][0]['termination'] = None
    elif damage == 'extra':
        proof['caller_claim'] = 'stopped'
    elif damage == 'boolean-exit':
        proof['actual_exit_code'] = True
    elif damage == 'missing-field':
        del proof['actor']
    elif damage in ('integer-child', 'null-group', 'integer-capture',
                    'negative-iteration', 'null-schema', 'string-exit',
                    'boolean-iteration'):
        field, value = {
            'integer-child': ('child_reaped', 1),
            'null-group': ('process_group_stopped', None),
            'integer-capture': ('capture_complete', 1),
            'negative-iteration': ('iteration', -1),
            'null-schema': ('schema', None),
            'string-exit': ('actual_exit_code', '0'),
            'boolean-iteration': ('iteration', True),
        }[damage]
        proof[field] = value
    elif damage in ('child_reaped', 'process_group_stopped', 'capture_complete'):
        proof[damage] = False
    elif damage == 'iteration':
        proof[damage] = 99
    else:
        proof[damage] = 'foreign-proof'
    patch_pending(tools, altered)
    before = state(tools)
    with pytest.raises(PoiseError, match='termination|proof|quiescence|identity'):
        restart(tools, 'T1', before['task']['version'], f'damaged-{damage}',
                authorization={'role': 'user', 'decision':
                               'Fixture user permits recovery; owner proof still required.'})
    assert state(tools) == before
    assert calls == [pending['runs'][0]['run_id']]


@pytest.mark.parametrize('damage', ['unstarted-proof', 'duplicate-run', 'nonsequential-starts'],
                         ids=str)
def test_termination_run_topology_is_not_permission(project, monkeypatch, damage):
    tools, _, pending, calls = _pending(project, monkeypatch)
    assert_proof(pending)
    altered = deepcopy(pending)
    if damage == 'unstarted-proof':
        altered['runs'][0]['started'] = False
    elif damage == 'duplicate-run':
        altered['runs'].append(deepcopy(altered['runs'][0]))
    else:
        altered['runs'].insert(0, {
            'run_id': '00000000-0000-4000-8000-000000000002',
            'method_id': 'UNSTARTED', 'started': False, 'termination': None})
    patch_pending(tools, altered)
    before = state(tools)
    with pytest.raises(PoiseError, match='run|attempt|identity|termination|proof'):
        restart(tools, 'T1', before['task']['version'], f'topology-{damage}',
                authorization={'role': 'user', 'decision':
                               'Fixture permission cannot legalize damaged run topology.'})
    assert state(tools) == before
    assert calls == [pending['runs'][0]['run_id']]


def test_proved_transfer_archives_mixed_negative_missing_and_unstarted(project, monkeypatch):
    project['task']['planning'] = {
        'schema': 'task-planning-1', 'template': None,
        'restart_revision_policy': {'reviewer': ['process'], 'user': ['process']},
    }
    project['cfg']['runtime_services']['check_runner'].update(
        initial_seconds=2, progress_gap_seconds=0.1, poll_seconds=0.02)
    tools, context = _scenario(project,
        command=[sys.executable, '-S', '-c', 'import time; time.sleep(2)'],
        extra_command=[sys.executable, '-S', '-c', 'print("second-stopped")'],
        third_command=[sys.executable, '-S', '-c', 'print("must-not-start")'])
    record = tools.runtime.evidence_commands.record_receipt

    def lose_second(task_id, actor, stage, iteration, receipt):
        if receipt['method'] == 'CHECK2':
            raise OSError('missing second terminal receipt')
        return record(task_id, actor, stage, iteration, receipt)

    monkeypatch.setattr(tools.runtime.evidence_commands, 'record_receipt', lose_second)
    with pytest.raises(OSError, match='missing second'):
        verify(tools, result(context, 'Keep failed, missing and unstarted runs.'))
    pending = deepcopy(tools.runtime.current_task()['pending'])
    assert pending['version'] == 2
    first, second, third = pending['runs']
    assert (first['started'], second['started'], third['started']) == (True, True, False)
    first_receipt, = tools.runtime.evidence_commands.list_for('T1')
    assert first_receipt['timed_out'] is True
    assert first_receipt['passed'] is False
    assert_proof(pending, 0, exit_code=-9)
    assert_proof(pending, 1)
    assert third.get('termination') is None
    before = state(tools)
    assert _handoff(tools, pending, request_id='mixed-proof-transfer')['status'] == 'handed_off'
    reviewer = WorkTools(WorkPoise(project['config_path'], 'mixed-proof-reviewer'))
    reviewer.runtime.bootstrap({'id': 'T1'})
    current = reviewer.runtime.current_task()
    packet = dict(authorization={'role': 'reviewer', 'decision':
                  'Inspect original owner proof and effects; archive unknown without replay.'})
    answer = restart(reviewer, 'T1', current['version'], 'mixed-proof-archive', **packet)
    assert answer['status'] == 'newborn'
    after = reviewer.runtime.task_queries.record('T1')
    assert after['restart_history'][-1]['abandoned_check_attempt'] == pending
    assert reviewer.runtime.evidence_commands.list_for('T1') == [first_receipt]
    registry = reviewer.runtime.task_queries.verification_registry('T1')
    assert registry['active_contract'] is False
    assert registry['current'] == []
    assert registry['historical_registry']['revision'] == before['registry']['revision']
    assert registry['historical_registry']['entries'] == before['registry']['current']
    assert after['worktree'] == before['task']['worktree']
    assert after['history'][:len(before['task']['history'])] == before['task']['history']
    replay = restart(reviewer, 'T1', current['version'], 'mixed-proof-archive', **packet)
    assert replay['replayed'] is True
    assert reviewer.runtime.task_queries.record('T1') == after


@pytest.mark.parametrize('component', ['environment', 'source', 'definition'], ids=str)
def test_identity_diagnostics_are_truthful_and_do_not_expose_secrets(project, monkeypatch, component):
    tools, payload, calls = candidate(project, monkeypatch)
    fail_once(monkeypatch, tools.runtime.evidence_commands, 'record_receipt')
    with pytest.raises(OSError):
        verify(tools, deepcopy(payload))
    secret = 'private-diagnostic-sentinel'
    if component == 'environment':
        monkeypatch.setenv('HOME', secret)
    elif component == 'source':
        root = Path(tools.runtime.current_task()['worktree'])
        (root / 'src' / 'identity-change.txt').write_text('new source\n')
        git(root, 'add', 'src/identity-change.txt')
        git(root, 'commit', '-m', 'Change disposable source identity')
    else:
        # Change only the disposable registry definition, preserving its JSON shape.
        with tools.runtime.store.transaction() as database:
            row = database.execute(
                'SELECT data FROM task_methods WHERE task_id=? AND method_id=?',
                ('T1', 'CHECK')).fetchone()
            entry = json.loads(row[0])
            entry['method']['stdout_contains'] = [secret]
            database.execute('UPDATE task_methods SET data=? WHERE task_id=? AND method_id=?',
                             (json.dumps(entry), 'T1', 'CHECK'))
    before = state(tools)
    with pytest.raises(PoiseError) as caught:
        verify(tools, deepcopy(payload))
    diagnostic = str(caught.value)
    assert f'component={component}' in diagnostic
    for other in {'source', 'definition', 'environment'} - {component}:
        assert f'component={other}' not in diagnostic
    assert secret not in diagnostic
    assert state(tools) == before
    assert len(calls) == 1


@pytest.mark.parametrize(('changes', 'complete'), [
    ({'timed_out': True, 'actual_exit_code': -9}, True),
    ({'actual_exit_code': -15}, True),
    ({'capture_complete': False}, False),
    ({'capture_complete': None}, False),
    ({'timed_out': True, 'passed': True}, False),
], ids=['timeout', 'signal', 'incomplete', 'unknown-capture', 'contradiction'])
def test_terminal_completeness_does_not_assert_success(changes, complete):
    receipt = deepcopy(CONTRACT['terminal_receipt'])
    receipt.update(changes)
    assert completed_receipts([receipt]) is complete


def test_eight_stage_restart_preserves_historical_finding_without_current_credit(project):
    from batch.helpers import configure

    configure(project)
    source = Path(__file__).parent / 'fixtures' / 'eight_recovery_stages.json'
    process = json.loads(source.read_text())
    write_json(project['config_path'].parent / 'config/processes/development.json', process)
    project['cfg']['automatic_checks'] = []
    write_json(project['config_path'], project['cfg'])
    task = deepcopy(project['task'])
    task.update(process_contract('development', process))
    task['planning'] = deepcopy(CONTRACT['planning'])
    executor = WorkTools(WorkPoise(project['config_path'], 'history-executor'))
    initial = executor.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert verify(executor, result(initial, 'Original baseline report.'))['status'] == 'verified'
    executor.invoke(request('handoff', {'request_id': 'history-review',
        'reason': 'Independent baseline review.', 'result': None,
        'commit_message': None, 'artifact_paths': []}))
    reviewer = WorkTools(WorkPoise(project['config_path'], 'history-reviewer'))
    reviewer.runtime.bootstrap({'id': 'T1'})
    reviewer.invoke(request('advance', {
        'request_id': 'eight-stages-before-restart', 'task_id': 'T1',
        'target_stage': 'baseline_inspection'}))
    context = reviewer.invoke(request('bootstrap', {
        'task': None, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert context['stage'] == 'baseline_inspection'
    payload = result(context, 'Historical finding must remain in original evidence.')
    payload['stage_work'] = {
        'coverage': 'Inspected original baseline.',
        'findings': [{'id': 'F-HISTORY', 'subject': 'baseline proof',
                      'description': 'Original proof obligation was not satisfied.',
                      'evidence': 'The original baseline report lacks its proof.'}],
        'resolution_decisions': []}
    assert verify(reviewer, payload)['stage_outcome'] == 'changes_requested'
    before = reviewer.runtime.task_queries.record('T1')
    with reviewer.runtime.store.transaction() as database:
        historical = [tuple(row) for row in database.execute(
            'SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id',
            ('T1',))]
    assert any('F-HISTORY' in row[1] for row in historical)
    newborn = restart(reviewer, 'T1', before['version'], 'restart-eight-obligations',
        authorization={'role': 'reviewer', 'decision':
                       'Preserve historical finding and repeat all eight obligations.'})
    assert newborn['status'] == 'newborn'
    assert newborn['process'] == process
    assert len(newborn['process']['stages']) == 8
    ready = reviewer.invoke(request('task', {'action': 'ready',
        'request_id': 'ready-eight-obligations', 'task_id': 'T1',
        'expected_revision': newborn['revision']}))
    assert ready['status'] == 'available'
    fresh = executor.runtime.bootstrap({'id': 'T1'})
    assert fresh['stage'] == 'baseline'
    assert fresh['workflow']['visits'] == {
        'baseline': 1, 'baseline_inspection': 0, 'solution_planning': 0,
        'solution_planning_inspection': 0, 'test_implementation': 0,
        'test_inspection': 0, 'implementation': 0, 'implementation_inspection': 0}
    assert fresh['workflow']['feedback']['findings'] == []
    assert fresh['workflow']['evidence']['assessment'] is None
    after = executor.runtime.task_queries.record('T1')
    assert after['process'] == before['process']
    assert after['requirements_snapshot'] == before['requirements_snapshot']
    assert after['requirements_agreement'] == before['requirements_agreement']
    with executor.runtime.store.transaction() as database:
        saved = [tuple(row) for row in database.execute(
            'SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id',
            ('T1',))]
    assert saved == historical
    target = {'request_id': 'repeat-all-eight-obligations', 'task_id': 'T1',
              'target_stage': 'implementation_inspection'}
    names = ['baseline', 'baseline_inspection', 'solution_planning',
             'solution_planning_inspection', 'test_implementation',
             'test_inspection', 'implementation', 'implementation_inspection']
    for index, stage in enumerate(names):
        actor = executor if index % 2 == 0 else reviewer
        actor.invoke(request('advance', target))
        context = actor.invoke(request('bootstrap', {
            'task': None, 'decision': None, 'feedback': None, 'rework_stage': None}))
        assert context['stage'] == stage
        assert context['status'] == 'active'
        fresh_payload = result(context, f'Fresh obligation {index + 1}.')
        if index % 2:
            fresh_payload['stage_work'] = {
                'coverage': 'Independent fresh-stage proof.', 'findings': [],
                'resolution_decisions': []}
        assert verify(actor, fresh_payload)['status'] == 'verified'
        if index < 7:
            assert actor.invoke(request('advance', target))['status'] == 'role_handoff_required'
            assert actor.invoke(request('handoff', {
                'request_id': f'fresh-eight-handoff-{index}',
                'reason': 'Transfer to the distinct owner of the next stage.',
                'result': None, 'commit_message': None, 'artifact_paths': []
            }))['status'] == 'handed_off'
            recipient = reviewer if index % 2 == 0 else executor
            recipient.runtime.bootstrap({'id': 'T1'})
    with reviewer.runtime.store.transaction() as database:
        final_rows = [tuple(row) for row in database.execute(
            'SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id',
            ('T1',))]
    assert final_rows[:len(historical)] == historical
    assert len(final_rows) == len(historical) + 8
    final_context = reviewer.invoke(request('bootstrap', {
        'task': None, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert final_context['workflow']['visits'] == {
        stage: 1 for stage in names}
