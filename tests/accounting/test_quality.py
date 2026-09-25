"""Observed late-finding costs use immutable facts, not optional-DB Task copies."""
from copy import deepcopy
from pathlib import Path
from threading import local

import pytest

from conftest import write_json
from poise.runtime import Poise
from poise.application.work import WorkTools
from tests.runner.helpers import process, inspect, finding, resolution, decision
from tests.batch.helpers import request
from .test_paths import DeterministicClock, metrics, send
from .test_domain import sample


def verify(work, context, stage_work):
    value = deepcopy(context['result_template'])
    value['sections']['report'] = 'Current result'
    value['commit_message'] = 'feat: result'
    value['stage_work'] = stage_work
    return work.invoke(request('verify', {'result': value, 'artifacts': []}))


def advance(work):
    return work.invoke(request('bootstrap', {
        'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None}))


def quality_session(project, mode):
    project['cfg']['accounting']['time_mode'] = mode
    p = process('development')
    project['cfg']['automatic_checks'] = []
    for stage in p['stages']:
        stage['role'] = 'executor'
    write_json(project['root']/'config/processes/development.json', p)
    write_json(project['config_path'], project['cfg'])
    task = deepcopy(project['task'])
    task.update(methods=[], method_inputs=[], checks={s['id']: [] for s in p['stages']})
    task['evidence_plan'] = {s['id']: {
        'subject_methods': {}, 'arguments': [], 'review_arguments': []} for s in p['stages']}
    task['decomposition']['phases'] = [
        {'stage': s['id'], 'skills': ['task-domain'], 'areas': []} for s in p['stages']]
    task['stage_contracts'] = [{'stage_id': s['id'], 'allowed_paths': list(s['allowed_paths']),
        'entry_requirements': [], 'exit_requirements': []} for s in p['stages']]
    runtime = Poise(project['config_path'], 'Q', DeterministicClock())
    work = WorkTools(runtime)
    context = work.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None}))
    (Path(context['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    assert verify(work, context, {})['status'] == 'verified'
    context = advance(work)
    assert verify(work, context, inspect([finding('F1'), finding('F2')]))['stage_outcome'] == 'changes_requested'
    context = advance(work)
    runtime.telemetry.flush()
    assert context['stage'] == 'amend'
    return runtime, work, context


def targets(stage='draft', iteration=1, finding_id='F1'):
    return [{'finding_id': finding_id, 'stage': stage, 'iteration': iteration}]


@pytest.mark.parametrize('mode', ['tool_cycle', 'reported'])
def test_only_explicit_late_findings_count_and_group_cost_is_not_divided(project, mode):
    runtime, work, context = quality_session(project, mode)
    assert metrics(work)['tasks'][0]['quality_findings_count'] == 0
    span = {'source': 'test', 'stream': 'fix-time', 'event_id': 't1',
            'started_at': '2026-09-07T10:00:00Z', 'ended_at': '2026-09-07T10:10:00Z'}
    attributed = targets() + targets(finding_id='F2')
    for _ in range(2):
        send(work, [sample()], cause='delivered_rework',
             intervals=[span] if mode == 'reported' else [], finding_targets=attributed)
    runtime.telemetry.flush()
    report = metrics(work)
    assert report['telemetry']['failed'] == 0
    assert report['tasks'][0]['quality_findings_count'] == 2
    assert report['totals']['by_cause']['delivered_rework']['model_tokens'] == 120
    assert report['totals']['remediation_groups'][0]['finding_ids'] == ['F1', 'F2']
    assert report['totals']['remediation_groups'][0]['model_tokens'] == 120
    if mode == 'reported':
        assert report['totals']['remediation_groups'][0]['active_seconds'] == 600
        assert report['totals']['by_cause']['delivered_rework']['active_seconds'] == 600
    assert verify(work, context, {'resolutions': [resolution('R1', 'F1'), resolution('R2', 'F2')]})['status'] == 'verified'
    context = advance(work)
    assert verify(work, context, inspect(decisions=[decision('R1'), decision('R2')]))['status'] == 'verified'
    work.invoke(request('accept', {}))
    runtime.telemetry.flush()
    assert metrics(work)['tasks'][0]['resolved_quality_findings_count'] == 2


@pytest.mark.parametrize('invalid', [
    targets(finding_id='unknown'), targets(stage='never-delivered'), targets(stage='audit'),
])
def test_invalid_finding_reference_rejects_whole_optional_batch_without_changing_work(project, invalid):
    runtime, work, _ = quality_session(project, 'reported')
    before = runtime.task_queries.record('T1')
    assert send(work, [sample()], cause='delivered_rework', finding_targets=invalid)['status'] == 'read_only'
    runtime.telemetry.flush()
    report = metrics(work)
    assert report['totals']['model_tokens'] is None
    assert report['tasks'][0]['quality_findings_count'] == 0
    assert report['telemetry']['failed'] == 1
    assert runtime.task_queries.record('T1') == before


def test_finding_worker_does_not_read_or_write_authoritative_storage(project, monkeypatch):
    runtime, work, _ = quality_session(project, 'reported')
    worker = local()
    observed = []
    database = runtime.store.database
    transaction = database.transaction
    process = runtime.telemetry.dispatcher.processor.process

    def guarded_transaction():
        assert not getattr(worker, 'active', False), 'optional worker touched Task DB'
        return transaction()

    def observed_process(envelope):
        worker.active = True
        try:
            observed.append(envelope)
            return process(envelope)
        finally:
            worker.active = False

    monkeypatch.setattr(database, 'transaction', guarded_transaction)
    monkeypatch.setattr(runtime.telemetry.dispatcher.processor, 'process', observed_process)
    send(work, [sample()], cause='delivered_rework', finding_targets=targets())
    runtime.telemetry.flush()
    report = metrics(work)
    assert observed
    assert report['telemetry']['failed'] == 0
    assert report['tasks'][0]['quality_findings_count'] == 1
    assert report['totals']['model_tokens'] == 120
    # No shadow Task rows or authoritative classification writes are introduced.
    with runtime.accounting.port.telemetry_repo.database.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    with runtime.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM accounting_findings').fetchone()[0] == 0
