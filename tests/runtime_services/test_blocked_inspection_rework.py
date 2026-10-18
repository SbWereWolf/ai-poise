"""A persisted inspection can request registry repair without fabricated checks."""
from pathlib import Path
from dataclasses import replace
import json
from copy import deepcopy

import pytest

from conftest import add_test, write_json
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError, VersionConflict
from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository, SqliteTaskRepository
from poise.modules.tasks.domain import TaskStatus
from tests.batch.helpers import bootstrap, request, result, verify
from tests.batch.test_registry_atomicity import database_snapshot
from tests.verification.test_current_registry_mutation import (
    configure_public_registry_case, public_tools, change, operation,
    focused_product_registration, OBLIGATIONS,
)
from .test_participant_continuity import release_packet


CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / 'fixtures/invalid-check-recovery.json').read_text()
)


@pytest.fixture
def blocked(project):
    configure_public_registry_case(project)
    return blocked_inspection(project)


def blocked_inspection(project, *, incomplete_coverage=False):
    executor = public_tools(project, 'registry-executor')
    context = bootstrap(executor, project)
    add_test(context['worktree'])
    payload = result(context)
    if incomplete_coverage:
        green = focused_product_registration(project, ('requirements[0]',))
        payload['method_additions'] = change(
            operation('replace', 'GREEN', registration=green),
            operation('remove', 'CLEANUP_FULL_GREEN'),
        )
    else:
        payload['method_additions'] = change(operation('remove', 'GREEN'),
                                            operation('remove', 'CLEANUP_FULL_GREEN'))
    assert verify(executor, payload)['status'] == 'verified'
    executor.invoke(release_packet(executor))
    reviewer = public_tools(project, 'registry-reviewer')
    reviewer.invoke(request('bootstrap', {
        'task': {'id': context['task']}, 'decision': None,
        'feedback': None, 'rework_stage': None}))
    inspection = reviewer.invoke(request('bootstrap', {
        'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None}))
    assert inspection['stage'] == 'test_inspection'
    candidate = result(inspection, 'The current registry prevents inspection exit.')
    candidate['stage_work'] = {'coverage': 'Inspect the current executable coverage.',
        'findings': [{'id': 'missing-green', 'subject': 'current registry',
          'description': 'Restore executable GREEN coverage in the registry-owning stage.',
          'evidence': 'No current GREEN executable test remains.'}],
        'resolution_decisions': []}
    if incomplete_coverage:
        candidate['stage_work']['findings'] = [deepcopy(CONTRACT['coverage_finding'])]
    expected = r'definition_of_done\[0\]' if incomplete_coverage else 'GREEN executable-test'
    with pytest.raises(PoiseError, match=expected):
        verify(reviewer, candidate)
    runtime = reviewer.runtime
    with runtime.task_commands.unit_of_work() as uow:
        task = uow.tasks.load(context['task'])
    assert task.state.status == TaskStatus.ACTIVE and task.state.submission_digest
    return reviewer, inspection, task


def rework(tools, target='test_implementation'):
    return tools.invoke(request('bootstrap', {'task': None, 'decision': 'rework',
        'feedback': 'Repair the inspection-blocking registry while retaining the candidate.',
        'rework_stage': target}))


def test_public_registry_repair_preserves_candidate_findings_evidence_and_wip(blocked):
    tools, inspection, task = blocked
    assert task.progress.outcome == 'changes_requested'
    assert task.state.status.value == 'active'
    with tools.runtime.store.transaction() as database:
        candidate = tuple(database.execute(
            "SELECT seq,stage,iteration,data FROM submissions WHERE task_id=? ORDER BY seq DESC LIMIT 1",
            (inspection['task'],),
        ).fetchone())
        assert tuple(database.execute(
            "SELECT claimed_by,status FROM tasks WHERE id=?", (inspection['task'],),
        ).fetchone()) == ('registry-reviewer', 'active')
    assert candidate[1:3] == ('test_inspection', 1)
    assert json.loads(candidate[3])['stage_work']['findings'] == [CONTRACT['finding']]
    before = database_snapshot(tools.runtime)
    worktree = Path(inspection['worktree'])
    wip = worktree / 'tests' / 'operator-note.txt'
    wip.write_text('Uncommitted author material must survive rework.')
    repaired = rework(tools)
    assert repaired['stage'] == 'test_implementation' and repaired['iteration'] == 2
    assert repaired['task'] == inspection['task'] and repaired['worktree'] == str(worktree)
    assert wip.read_text() == 'Uncommitted author material must survive rework.'
    assert tools.runtime.current_task()['claimed_by'] == tools.runtime.session
    assert any(item['id'] == 'missing-green'
               for item in repaired['workflow']['feedback']['open_findings'])
    after = database_snapshot(tools.runtime)
    for table in ('submissions', 'evidence', 'task_results'):
        assert after[table] == before[table]
    assert all(row in after['task_events'] for row in before['task_events'])
    with tools.runtime.store.transaction() as database:
        assert tuple(database.execute(
            "SELECT seq,stage,iteration,data FROM submissions WHERE seq=?", (candidate[0],),
        ).fetchone()) == candidate
        event = json.loads(database.execute(
            "SELECT data FROM task_events WHERE task_id=? ORDER BY seq DESC LIMIT 1",
            (inspection['task'],),
        ).fetchone()[0])
    assert event['event'] == 'user_inspection_registry_rework'
    assert event['actor'] == 'registry-reviewer'
    show = tools.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    assert show['results'][0]['value']['task'] == inspection['task']
    assert tools.invoke(release_packet(tools, 'preserve-registry-repair'))['status'] == 'ownership_released'


@pytest.mark.parametrize('target', ['implementation', 'absent', None])
def test_public_recovery_rejects_non_registry_or_undeclared_target_atomically(blocked, target):
    tools, _, _ = blocked
    before = database_snapshot(tools.runtime)
    with pytest.raises(PoiseError):
        rework(tools, target)
    assert database_snapshot(tools.runtime) == before


def test_recovery_rechecks_gate_inside_command_transaction(blocked, monkeypatch):
    tools, _, task = blocked
    # Change only the inspection-gate owner's observation between public routing
    # and the transactional command; no stale preflight can authorize recovery.
    monkeypatch.setattr(type(task.check_registry), 'validate_inspection_exit', lambda *a, **k: None)
    before = database_snapshot(tools.runtime)
    with pytest.raises(PoiseError):
        tools.runtime.task_commands.rework(task.state.task_id, tools.runtime.session,
            'Gate no longer blocked.', tools.runtime._current_tree(tools.runtime.current_task()),
            'test_implementation')
    assert database_snapshot(tools.runtime) == before


def test_pending_external_operation_cannot_be_cleared_by_registry_recovery(blocked):
    tools, _, _ = blocked
    current = tools.runtime.current_task()
    with tools.runtime.task_commands.unit_of_work() as uow:
        uow.execution.patch(current['id'], {'pending': 'checks'})
    before = database_snapshot(tools.runtime)
    with pytest.raises(PoiseError, match='прерван|pending|outcome'):
        rework(tools)
    assert database_snapshot(tools.runtime) == before


def test_command_rolls_back_transition_and_feedback_on_execution_save_failure(blocked, monkeypatch):
    tools, _, task = blocked
    before = database_snapshot(tools.runtime)
    original = SqliteExecutionRepository.patch

    def fail_after_write(repository, task_id, updates):
        original(repository, task_id, updates)
        raise RuntimeError("injected execution persistence failure")

    monkeypatch.setattr(SqliteExecutionRepository, 'patch', fail_after_write)
    with pytest.raises(RuntimeError, match='injected'):
        tools.runtime.task_commands.rework(task.state.task_id, tools.runtime.session,
            'Repair gate.', tools.runtime._current_tree(tools.runtime.current_task()),
            'test_implementation')
    assert database_snapshot(tools.runtime) == before


def test_direct_command_rejects_pending_outcome_without_erasing_it(blocked):
    tools, _, task = blocked
    with tools.runtime.task_commands.unit_of_work() as uow:
        uow.execution.patch(task.state.task_id, {'pending': 'checks'})
    before = database_snapshot(tools.runtime)
    with pytest.raises(PoiseError, match='прерван|pending|outcome'):
        tools.runtime.task_commands.rework(task.state.task_id, tools.runtime.session,
            'Repair gate.', 'current-tree', 'test_implementation')
    assert database_snapshot(tools.runtime) == before


def test_domain_requires_owned_submitted_gated_inspection_and_registry_target(blocked):
    tools, _, task = blocked
    actor = tools.runtime.session
    variants = [
        replace(task, state=replace(task.state, submission_digest=None)),
        replace(task, progress=replace(task.progress, stage_work=None)),
        replace(task, check_registry=replace(task.check_registry, inspection_stages=())),
    ]
    for current in variants:
        with pytest.raises(DomainError):
            current.rework(actor, 'Repair gate.', 'test_implementation')
    for target in (None, 'implementation', 'absent'):
        with pytest.raises(DomainError):
            task.rework(actor, 'Repair gate.', target)
    for feedback in ('', '   ', None):
        with pytest.raises(DomainError):
            task.rework(actor, feedback, 'test_implementation')
    with pytest.raises(DomainError, match='другой сессией'):
        task.rework('foreign-owner', 'Repair gate.', 'test_implementation')
    changed = task.rework(actor, 'Repair gate.', 'test_remediation')
    assert changed.task.stage.stage_id == 'test_remediation'
    assert changed.events[0].kind == 'user_inspection_registry_rework'
    assert changed.task.evidence_book == task.evidence_book


def test_pending_resolution_guard_applies_before_unverified_candidate_decisions(blocked):
    tools, _, task = blocked
    finding = Finding('old-finding', 'registry', 'Repair registry', 'old evidence', 'test_inspection', 1)
    resolution = Resolution('pending-resolution', 'old-finding', 'Proposed repair', 'proof', 'test_remediation', 1)
    book = FeedbackBook((finding,), (resolution,), ())
    work = json.loads(task.progress.stage_work)
    work['resolution_decisions'] = [{'resolution_id': resolution.id, 'decision': 'accepted',
                                    'reason': 'Cannot bypass inspection with this candidate.'}]
    current = replace(task, feedback=book, progress=replace(task.progress, stage_work=json.dumps(work)))
    with pytest.raises(DomainError, match='ещё не осмотрены'):
        current.rework(tools.runtime.session, 'Repair gate.', 'test_implementation')
    assert current.feedback.pending_resolutions == (resolution,)


def test_candidate_cannot_introduce_uninspected_resolution_then_escape(blocked):
    tools, _, task = blocked
    finding = Finding('old-finding', 'registry', 'Repair registry', 'old evidence', 'test_inspection', 1)
    work = {'coverage': 'A proposed repair is not an inspected repair.', 'findings': [],
            'resolution_decisions': [], 'resolutions': [{'id': 'proposed',
                'finding_id': finding.id, 'description': 'Repair', 'evidence': 'proposal'}]}
    current = replace(task, feedback=FeedbackBook((finding,), (), ()),
                      progress=replace(task.progress, stage_work=json.dumps(work)))
    with pytest.raises(DomainError, match='ещё не осмотрены'):
        current.rework(tools.runtime.session, 'Repair gate.', 'test_implementation')


def test_gate_is_rechecked_after_successful_public_preflight(blocked, monkeypatch):
    tools, _, task = blocked
    original = tools.runtime.task_commands.inspection_registry_rework_available

    def stale_preflight(task_id, target):
        assert original(task_id, target)
        monkeypatch.setattr(type(task.check_registry), 'validate_inspection_exit', lambda *a, **k: None)
        return True

    monkeypatch.setattr(tools.runtime.task_commands, 'inspection_registry_rework_available', stale_preflight)
    before = database_snapshot(tools.runtime)
    with pytest.raises(PoiseError, match='reproducible'):
        rework(tools)
    assert database_snapshot(tools.runtime) == before


def test_registry_owner_error_is_not_mistaken_for_a_reproducible_gate(blocked, monkeypatch):
    tools, _, task = blocked

    def broken_owner(*args, **kwargs):
        raise RuntimeError('unexpected owner defect')

    monkeypatch.setattr(type(task.check_registry), 'validate_inspection_exit', broken_owner)
    with pytest.raises(RuntimeError, match='unexpected'):
        task.rework(tools.runtime.session, 'Repair gate.', 'test_implementation')


def test_registry_gate_recovery_does_not_require_a_fabricated_finding(blocked):
    tools, _, task = blocked
    work = {'coverage': 'No defect except the current registry gate.',
            'findings': [], 'resolution_decisions': []}
    current = replace(task, progress=replace(task.progress, stage_work=json.dumps(work)))
    changed = current.rework(tools.runtime.session, 'Restore coverage.', 'test_implementation')
    assert changed.task.feedback == current.feedback
    assert changed.task.state.status == TaskStatus.ACTIVE


def test_public_registry_repair_handles_incomplete_coverage(project):
    configure_public_registry_case(project)
    tools, inspection, task = blocked_inspection(project, incomplete_coverage=True)
    before = database_snapshot(tools.runtime)
    assert task.progress.outcome == 'changes_requested'
    assert task.state.status.value == 'active'
    repaired = rework(tools)
    assert repaired['stage'] == 'test_implementation'
    assert repaired['iteration'] == 2
    assert repaired['task'] == inspection['task']
    assert repaired['worktree'] == inspection['worktree']
    findings = repaired['workflow']['feedback']['open_findings']
    assert len(findings) == 1
    assert {key: findings[0][key] for key in CONTRACT['coverage_finding']} == CONTRACT['coverage_finding']
    assert findings[0]['stage'] == 'test_inspection'
    after = database_snapshot(tools.runtime)
    for table in ('submissions', 'evidence', 'task_results', 'task_methods'):
        assert after[table] == before[table]
    assert all(row in after['task_events'] for row in before['task_events'])


def test_transactional_recovery_rejects_stale_task_version_without_writes(blocked, monkeypatch):
    tools, _, task = blocked
    before = database_snapshot(tools.runtime)
    original = SqliteTaskRepository.save
    observed = []

    def stale_compare(repository, change_, expected_version):
        observed.append('real_repository_version_comparison')
        return original(repository, change_, expected_version - 1)

    monkeypatch.setattr(SqliteTaskRepository, 'save', stale_compare)
    with pytest.raises(VersionConflict, match='Конфликт версии задачи'):
        tools.runtime.task_commands.rework(
            task.state.task_id, tools.runtime.session, 'Restore coverage.',
            tools.runtime._current_tree(tools.runtime.current_task()), 'test_implementation',
        )
    assert observed == ['real_repository_version_comparison']
    assert database_snapshot(tools.runtime) == before


def test_transactional_recovery_rejects_foreign_owner_without_writes(blocked, project):
    tools, _, task = blocked
    foreign = public_tools(project, 'registry-foreign')
    before = database_snapshot(tools.runtime)
    with pytest.raises(DomainError, match='другой сессией'):
        foreign.runtime.task_commands.rework(
            task.state.task_id, foreign.runtime.session, 'Restore coverage.',
            tools.runtime._current_tree(tools.runtime.current_task()), 'test_implementation',
        )
    assert database_snapshot(tools.runtime) == before
    assert foreign.runtime.current_task() is None


def configure_repair_plan(project):
    configure_public_registry_case(project)
    workflow = project['workflow']
    inspector = next(s for s in workflow['stages'] if s['id'] == 'test_inspection')
    inspector['transitions']['changes_requested'] = 'repair_plan'
    inspector['rework_targets'].append('repair_plan')
    for node in deepcopy(CONTRACT['plan_stages']):
        workflow['stages'].append(node)
        project['task']['checks'][node['id']] = []
        project['task']['evidence_plan'][node['id']] = {
            'subject_methods': {}, 'arguments': [], 'review_arguments': [],
        }
        project['task']['stage_contracts'].append({
            'stage_id': node['id'], 'allowed_paths': [],
            'entry_requirements': [], 'exit_requirements': [],
            'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []},
        })
        project['task']['decomposition']['phases'].append({
            'stage': node['id'], 'skills': ['task-domain'], 'areas': [],
        })
        project['cfg']['automatic_checks'][0]['by_stage'][node['id']] = []
    write_json(project['root'] / 'config/workflows/development.json', workflow)


def pass_to_stage(sender, receiver, task_id, request_id):
    assert transfer(sender, None, id=request_id, message_=None)['status'] == 'handed_off'
    receiver.invoke(request('bootstrap', {
        'task': {'id': task_id}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    return receiver.invoke(request('bootstrap', {
        'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None,
    }))


def repair_criteria(tools):
    return tools.invoke(request('show', {'queries': [{
        'id': 'criteria', 'kind': 'stage_criteria', 'stage_id': 'test_remediation',
    }]}))['results'][0]['value']


def criteria_work(tools, request_id, *, proposals=(), decisions=()):
    current = tools.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    return {
        'request_id': request_id,
        'expected_version': current['results'][0]['value']['version'],
        'proposals': list(proposals), 'decisions': list(decisions),
    }


def test_public_registry_repair_accepts_independent_plan_before_registry_correction(project):
    configure_repair_plan(project)
    reviewer, inspection, _ = blocked_inspection(project)
    executor = public_tools(project, 'registry-executor')
    initial = rework(reviewer, 'repair_plan')
    assert initial['stage'] == 'repair_plan'
    registry = reviewer.invoke(request('show', {'queries': [{
        'id': 'registry', 'kind': 'verification_registry',
    }]}))['results'][0]['value']
    registry_before = deepcopy(registry)
    assert [entry['method']['id'] for entry in registry['current']] == ['RED']
    assert registry['executable_obligations'] == ['requirements[0]', 'definition_of_done[0]']
    assert transfer(reviewer, None, id='initial-plan-owner', message_=None)['status'] == 'handed_off'
    plan = executor.invoke(request('bootstrap', {
        'task': {'id': inspection['task']}, 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))
    assert plan['stage'] == 'repair_plan'

    for attempt in (1, 2):
        state = repair_criteria(executor)
        assert state['revision'] == 0
        assert state['execution']['state'] == 'unstarted'
        payload = result(plan)
        payload['stage_work'] = {'criteria_work': criteria_work(
            executor, f'plan-proposal-{attempt}', proposals=[{
                'stage_id': 'test_remediation', 'expected_criteria_revision': 0,
                'criteria': deepcopy(CONTRACT['criteria']),
                'entry_requirements': [], 'exit_requirements': [],
            }],
        )}
        if attempt == 2:
            payload['stage_work']['resolutions'] = [{
                'id': 'plan-order-fixed', 'finding_id': 'plan-order',
                'description': 'Restore coverage after accepting this plan.',
                'evidence': 'The revised plan specifies the correction order.',
            }]
        assert verify(executor, payload)['status'] == 'verified'
        proposal = repair_criteria(executor)['proposals'][-1]
        decision = pass_to_stage(executor, reviewer, inspection['task'], f'plan-to-review-{attempt}')
        assert decision['stage'] == 'repair_plan_inspection'
        payload = result(decision)
        payload['stage_work']['coverage'] = 'Inspect only the repair plan and its criteria.'
        payload['stage_work']['criteria_work'] = criteria_work(
            reviewer, f'plan-decision-{attempt}', decisions=[{
                'proposal_id': proposal['proposal_id'],
                'proposal_digest': proposal['proposal_digest'],
                'decision': 'rejected' if attempt == 1 else 'accepted',
                'reason': 'Specify correction order.' if attempt == 1 else 'Correction order is explicit.',
            }],
        )
        if attempt == 1:
            payload['stage_work']['findings'] = [{
                'id': 'plan-order', 'subject': 'repair plan',
                'description': 'State when registry coverage is restored.',
                'evidence': 'Plan omits the registry correction order.',
            }]
        else:
            payload['stage_work']['resolution_decisions'] = [{
                'resolution_id': 'plan-order-fixed', 'decision': 'accepted',
                'reason': 'The plan now specifies the correction order.',
            }]
        reviewed = verify(reviewer, payload)
        assert reviewed['stage_outcome'] == ('changes_requested' if attempt == 1 else 'clear')
        unchanged = reviewer.invoke(request('show', {'queries': [{
            'id': 'registry', 'kind': 'verification_registry',
        }]}))['results'][0]['value']
        assert unchanged == registry_before
        plan = pass_to_stage(reviewer, executor, inspection['task'], f'plan-to-executor-{attempt}')
        assert plan['stage'] == ('repair_plan_revision' if attempt == 1 else 'test_remediation')

    accepted = repair_criteria(executor)
    assert accepted['criteria'] == CONTRACT['criteria']
    assert accepted['revision'] == 1
    assert accepted['execution']['state'] == 'started'
    assert accepted['execution']['bound_revision'] == 1
    assert [f['id'] for f in plan['workflow']['feedback']['open_findings']] == ['missing-green']
    payload = result(plan)
    payload['method_additions'] = change(
        operation('add', 'GREEN', registration=focused_product_registration(project, OBLIGATIONS)),
        request_id='actual-registry-correction', revision=registry_before['revision'],
    )
    payload['stage_work'] = {'resolutions': [{
        'id': 'registry-restored', 'finding_id': 'missing-green',
        'description': 'Restore complete GREEN definition coverage.',
        'evidence': 'Current registry contains complete GREEN executable coverage.',
    }]}
    assert verify(executor, payload)['status'] == 'verified'
    correction = pass_to_stage(executor, reviewer, inspection['task'], 'actual-correction-to-review')
    assert correction['stage'] == 'test_inspection'
    payload = result(correction)
    payload['stage_work']['coverage'] = 'Inspect the actual restored registry and original finding.'
    payload['stage_work']['resolution_decisions'] = [{
        'resolution_id': 'registry-restored', 'decision': 'accepted',
        'reason': 'Complete GREEN coverage is present.',
    }]
    final = verify(reviewer, payload)
    assert final['stage_outcome'] == 'clear'
    assert final['feedback']['open_findings'] == []
