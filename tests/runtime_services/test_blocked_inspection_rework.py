"""A persisted inspection can request registry repair without fabricated checks."""
from pathlib import Path
from dataclasses import replace
import json

import pytest

from conftest import add_test
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError
from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository
from poise.modules.tasks.domain import TaskStatus
from tests.batch.helpers import bootstrap, request, result, verify
from tests.batch.test_registry_atomicity import database_snapshot
from tests.verification.test_current_registry_mutation import (
    configure_public_registry_case, public_tools, change, operation,
)
from .test_handoff import transfer


@pytest.fixture
def blocked(project):
    configure_public_registry_case(project)
    executor = public_tools(project, 'registry-executor')
    context = bootstrap(executor, project)
    add_test(context['worktree'])
    payload = result(context)
    payload['method_additions'] = change(operation('remove', 'GREEN'),
                                        operation('remove', 'CLEANUP_FULL_GREEN'))
    assert verify(executor, payload)['status'] == 'verified'
    transfer(executor, None, message_=None)
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
    with pytest.raises(PoiseError, match='GREEN executable-test'):
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
    show = tools.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    assert show['results'][0]['value']['task'] == inspection['task']
    assert transfer(tools, None, id='preserve-registry-repair', message_='WIP: registry repair')['status'] == 'handed_off'


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
