"""Local publication records an accepted candidate; only integrate moves targets."""
from pathlib import Path
from copy import deepcopy
import subprocess

import pytest

from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.batch.test_registry_atomicity import database_snapshot
from tests.runtime_services.test_handoff import transfer
from .helpers import setup, verify, result, advance, start, inspect


@pytest.fixture
def publication(project):
    executor, context, plan, base = setup(project, conflict=False)
    produced = verify(executor, result(context, {
        'plan': plan, 'phase': 'prepare', 'resolutions': [], 'finding_resolutions': []}))
    assert produced['status'] == 'verified'
    transfer(WorkTools(executor), None, message_=None)
    reviewer = WorkPoise(project['config_path'], 'PUBLICATION-REVIEWER')
    start(reviewer, {'id': context['task']})
    inspected = inspect(reviewer, advance(reviewer))
    assert inspected['status'] == 'verified'
    context = advance(reviewer)
    assert context['stage'] == 'publish'
    payload = result(context, {'target_ref': 'refs/heads/main', 'expected_commit': base,
                              'authorization': 'User: record the accepted local candidate.'})
    return reviewer, context, payload, inspected


@pytest.mark.parametrize('remote_state', ['missing-remote', 'missing-remote-ref'])
def test_publication_without_remote_delivery_preserves_refs_candidate_and_authority(
    project, publication, monkeypatch, remote_state
):
    runtime, context, payload, inspected = publication
    if remote_state == 'missing-remote':
        git(project['app'], 'remote', 'remove', 'backup')
    else:
        git(project['remote'], 'update-ref', '-d', 'refs/heads/main')
    before_ref = git(project['app'], 'rev-parse', 'main')
    before_head = git(Path(context['worktree']), 'rev-parse', 'HEAD')
    original = subprocess.run

    def no_remote_commands(argv, *args, **kwargs):
        assert not any(a in ('ls-remote', 'push', 'fetch') for a in argv), argv
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', no_remote_commands)
    report = verify(runtime, payload)
    assert report['status'] == 'verified'
    assert report['commit'] == before_head == inspected['commit']
    assert report['verified_tree'] == inspected['verified_tree']
    assert git(project['app'], 'rev-parse', 'main') == before_ref
    action = report['action']
    assert action['status'] == 'complete'
    assert action['plan']['intent'] == payload['stage_work']
    receipt = action['steps'][0]['result']
    assert receipt['effect'] == 'local_record_only'
    assert receipt['remote_publication'] is False
    assert receipt['target_updated'] is False
    assert receipt['candidate'] == before_head
    assert receipt['tree'] == inspected['verified_tree']
    before = database_snapshot(runtime)
    assert verify(runtime, payload)['replayed'] is True
    assert verify(runtime, None)['replayed'] is True
    after = database_snapshot(runtime)
    for table in ('action_runs', 'action_events', 'task_results', 'submissions', 'evidence'):
        assert after[table] == before[table]


def test_local_target_drift_is_recorded_without_changing_either_branch(project, publication):
    runtime, context, payload, _ = publication
    (project['app'] / 'external.txt').write_text('Independent target change.')
    git(project['app'], 'add', 'external.txt')
    git(project['app'], 'commit', '-m', 'External target advancement')
    target = git(project['app'], 'rev-parse', 'HEAD')
    candidate = git(Path(context['worktree']), 'rev-parse', 'HEAD')
    report = verify(runtime, payload)
    assert report['status'] == 'action_blocked'
    assert report['action']['reason'] == 'target_changed'
    assert git(project['app'], 'rev-parse', 'HEAD') == target
    assert git(Path(context['worktree']), 'rev-parse', 'HEAD') == candidate


def test_invalid_authority_and_changed_reviewed_tree_reject_before_effects(publication):
    runtime, context, payload, _ = publication
    invalid = deepcopy(payload)
    invalid['stage_work']['authorization'] = ''
    before = database_snapshot(runtime)
    with pytest.raises(PoiseError):
        verify(runtime, invalid)
    assert database_snapshot(runtime) == before
    Path(context['worktree'], 'src/double.py').write_text('Unreviewed changes.')
    with pytest.raises(PoiseError):
        verify(runtime, payload)
    assert runtime.plan_actions.snapshot(runtime.current_task()) is None


def test_changed_authorization_does_not_rewrite_completed_publication(publication):
    runtime, _, payload, _ = publication
    assert verify(runtime, payload)['status'] == 'verified'
    before = database_snapshot(runtime)
    changed = deepcopy(payload)
    changed['stage_work']['authorization'] = 'Different authorization is not the recorded intent.'
    with pytest.raises(PoiseError):
        verify(runtime, changed)
    assert database_snapshot(runtime) == before


def test_changed_blocked_intent_rejects_before_submission(project, publication):
    runtime, _, payload, _ = publication
    (project['app'] / 'external.txt').write_text('Target drift.')
    git(project['app'], 'add', 'external.txt')
    git(project['app'], 'commit', '-m', 'Target drift')
    assert verify(runtime, payload)['status'] == 'action_blocked'
    changed = deepcopy(payload)
    changed['stage_work']['expected_commit'] = git(project['app'], 'rev-parse', 'HEAD')
    before = database_snapshot(runtime)
    with pytest.raises(PoiseError, match='immutable'):
        verify(runtime, changed)
    assert database_snapshot(runtime) == before


def test_unsubmitted_publication_cannot_create_receipt(publication):
    from poise.modules.actions.domain import Publication
    runtime, context, payload, inspected = publication
    before = database_snapshot(runtime)
    with pytest.raises(PoiseError, match='submitted'):
        runtime.plan_actions.commands.publish_local(
            context['task'], runtime.session, context['stage'], context['iteration'],
            Publication.parse(payload['stage_work']), inspected['commit'],
            inspected['verified_tree'], payload['stage_work']['expected_commit'])
    assert database_snapshot(runtime) == before


def test_receipt_write_failure_rolls_back_action_and_retry_is_safe(publication, monkeypatch):
    from poise.infrastructure.sqlite.actions import SqliteActionRepository
    runtime, context, payload, _ = publication
    original = SqliteActionRepository.create

    def fail_after_write(self, *args):
        original(self, *args)
        raise OSError('Interrupted receipt persistence')

    monkeypatch.setattr(SqliteActionRepository, 'create', fail_after_write)
    before = database_snapshot(runtime)
    with pytest.raises(OSError, match='Interrupted receipt'):
        verify(runtime, payload)
    after = database_snapshot(runtime)
    assert after['action_runs'] == before['action_runs']
    assert after['action_events'] == before['action_events']
    assert runtime.plan_actions.snapshot(runtime.current_task()) is None
    monkeypatch.setattr(SqliteActionRepository, 'create', original)
    assert verify(runtime, payload)['status'] == 'verified'
    assert verify(runtime, payload)['replayed'] is True


def test_empty_commit_cannot_replace_accepted_candidate(publication):
    runtime, context, payload, _ = publication
    git(Path(context['worktree']), 'commit', '--allow-empty', '-m', 'Unreviewed commit')
    before = database_snapshot(runtime)
    with pytest.raises(PoiseError, match='candidate differs'):
        verify(runtime, payload)
    assert database_snapshot(runtime) == before


def test_missing_local_target_is_not_created(publication):
    runtime, _, payload, _ = publication
    payload['stage_work']['target_ref'] = 'refs/heads/does-not-exist'
    report = verify(runtime, payload)
    assert report['status'] == 'action_blocked'
    assert report['action']['current']['observed_target'] is None
    assert runtime.plan_actions._read_optional_ref(
        Path(runtime.current_task()['worktree']), 'refs/heads/does-not-exist') is None
