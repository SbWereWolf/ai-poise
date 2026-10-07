"""Public Sprint reopening: independent persisted and file-effect oracles."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise as Poise, bind_task_requirements
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import restart
from sprints.helpers import bootstrap, draft, publish, setup, task, verify
from sprints.test_membership_conversion import add_execution


def client(project, actor):
    configure(project)
    return WorkTools(Poise(project['config_path'], actor))


@pytest.fixture
def planned(project):
    setup(project)
    planner = client(project, 'planner')
    edge = {'predecessor': 'A', 'successor': 'B', 'kind': 'completion'}
    born = draft(planner, [task(project, 'A'), task(project, 'B')], [edge])
    published = publish(planner, born['revision'])
    assert published['status'] == 'planned'
    assert published['eligible'] == ['A']
    return project, planner, published


def intent(revision, **changes):
    packet = json.loads((Path(__file__).parent / 'fixtures/unpublish-request.json').read_text())
    return {**packet, 'expected_revision': revision, **changes}


def invoke(planner, packet):
    return planner.invoke(request('sprint', packet))


def reopen(planner, revision, **changes):
    return invoke(planner, intent(revision, **changes))


def task_rows(planner):
    # Explicit columns and raw read-only values keep the preservation oracle
    # independent of the modified application projections/serializers.
    queries = {
        'tasks': 'SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks ORDER BY id',
        'execution': 'SELECT task_id,data,version FROM task_execution ORDER BY task_id',
        'submissions': 'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq',
        'results': 'SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id',
        'evidence': 'SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id',
        'events': 'SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq',
        'artifacts': 'SELECT id,owner,scope,path,digest FROM artifacts ORDER BY id',
        'members': 'SELECT sprint_id,task_id FROM sprint_members ORDER BY sprint_id,task_id',
        'owners': 'SELECT id,task_id FROM sessions ORDER BY id',
    }
    with planner.runtime.store.transaction() as db:
        return {name: [tuple(row) for row in db.execute(sql)] for name, sql in queries.items()}


def sprint_rows(planner):
    with planner.runtime.store.transaction() as db:
        return {
            'sprints': [tuple(row) for row in db.execute('SELECT id,state,revision,data FROM sprints ORDER BY id')],
            'layers': [tuple(row) for row in db.execute('SELECT sprint_id,revision,data,at FROM sprint_layers ORDER BY sprint_id,revision')],
            'dependencies': [tuple(row) for row in db.execute('SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies ORDER BY sprint_id,predecessor,successor,kind')],
            'requests': [tuple(row) for row in db.execute('SELECT sprint_id,request_id,digest,data FROM sprint_requests ORDER BY sprint_id,request_id')],
        }


def unchanged(planner):
    return task_rows(planner), sprint_rows(planner)


def handoff(worker, suffix):
    released = worker.invoke(request('handoff', {
        'request_id': f'release-{suffix}', 'reason': 'Release the actual fixture result.',
        'result': None, 'commit_message': None, 'artifact_paths': [],
    }))
    assert released['status'] == 'handed_off'


def prepare_member(project, state):
    worker = client(project, 'executor')
    context = bootstrap(worker, 'A')
    assert context['status'] == 'active'
    if state in ('verified', 'completed'):
        result = verify(worker, context)
        assert result['status'] == 'verified'
    if state == 'completed':
        assert worker.invoke(request('accept', {}))['status'] == 'completed'
    elif state == 'cancelled':
        assert worker.invoke(request('cancel', {'reason': 'Fixture cancellation.'}))['status'] == 'cancelled'
    else:
        handoff(worker, state)
    return context


def git_state(path):
    return tuple(subprocess.check_output(['git', '-C', str(path), *args]) for args in (
        ['rev-parse', 'HEAD'], ['show-ref'], ['status', '--porcelain=v1'], ['ls-files', '--stage'],
    ))


def test_reopen_available_preserves_every_task_and_real_graph(planned):
    _, planner, published = planned
    before = task_rows(planner)
    opened = reopen(planner, published['revision'])
    assert opened['sprint'] == 'S'
    assert opened['status'] == 'draft'
    assert opened['revision'] == published['revision'] + 1
    assert opened['eligible'] == []
    assert opened['dependencies'] == [{'predecessor': 'A', 'successor': 'B', 'kind': 'completion'}]
    assert task_rows(planner) == before
    record = planner.runtime.sprint_tools.commands.read('S', 'plan')['aggregate']
    assert record['state'] == 'draft'
    audit = record['decisions'][-1]
    assert audit['kind'] == 'unpublish'
    assert audit['reason'] == intent(0)['reason']
    assert audit['authorization'] == intent(0)['authorization']


@pytest.mark.parametrize('state', ['active', 'verified', 'completed', 'cancelled'])
def test_reopen_retains_started_and_terminal_members(planned, state):
    project, planner, published = planned
    prepare_member(project, state)
    assert planner.runtime.task_queries.record('A')['status'] == state
    before = task_rows(planner)
    opened = reopen(planner, published['revision'])
    assert opened['status'] == 'draft'
    assert task_rows(planner) == before
    assert planner.runtime.task_queries.record('A')['status'] == state


def test_reopen_preserves_unique_worktree_bytes_modes_refs_and_index(planned):
    project, planner, published = planned
    context = prepare_member(project, 'active')
    root = Path(context['worktree'])
    wip = root / 'src/unique-wip.txt'
    wip.write_bytes(b'unique uncommitted work\x00\xff\n')
    wip.chmod(0o640)
    before = git_state(root), wip.read_bytes(), wip.stat().st_mode, task_rows(planner)
    assert reopen(planner, published['revision'])['status'] == 'draft'
    assert (git_state(root), wip.read_bytes(), wip.stat().st_mode, task_rows(planner)) == before


@pytest.mark.parametrize('field,value,diagnostic', [
    ('reason', '', 'reason|основан'),
    ('authorization', '', 'authoriz|разреш'),
    ('expected_revision', 0, 'revision|ревиз'),
])
def test_reopen_refusal_preserves_authoritative_state(planned, field, value, diagnostic):
    _, planner, published = planned
    before = unchanged(planner)
    with pytest.raises(PoiseError, match=diagnostic):
        reopen(planner, published['revision'], **{field: value})
    assert unchanged(planner) == before


@pytest.mark.parametrize('actor', ['executor', 'other'])
def test_live_claim_including_requesting_actor_blocks_reopening(planned, actor):
    project, planner, published = planned
    worker = client(project, actor)
    bootstrap(worker, 'A')
    requester = worker if actor == 'executor' else planner
    before = unchanged(planner)
    with pytest.raises(PoiseError, match=r'\b(?:owned|owner|claim|claimed|ownership)\b|влад'):
        reopen(requester, published['revision'])
    assert unchanged(planner) == before


def test_independent_worktree_claim_blocks_reopening(planned):
    project, planner, published = planned
    prepare_member(project, 'active')
    holder = client(project, 'worktree-only')
    holder.runtime.ownership.acquire_worktree('A')
    assert planner.runtime.task_queries.record('A')['claimed_by'] is None
    assert holder.runtime.ownership.snapshot('worktree-only').worktree_task_id == 'A'
    before = unchanged(planner)
    with pytest.raises(PoiseError, match=r'\b(?:owned|owner|claim|claimed|ownership|worktree)\b|влад'):
        reopen(planner, published['revision'])
    assert unchanged(planner) == before


def test_cancelled_sprint_is_not_revived(planned):
    _, planner, published = planned
    cancelled = invoke(planner, {
        'action': 'cancel', 'sprint_id': 'S', 'request_id': 'cancel-S',
        'reason': 'Fixture Sprint is terminal.',
    })
    assert cancelled['status'] == 'cancelled'
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='published|cancel|опублик|отмен'):
        reopen(planner, cancelled['revision'])
    assert unchanged(planner) == before


def test_wrong_project_cannot_reopen_foreign_sprint(planned, monkeypatch):
    _, planner, published = planned
    # The declared application port supplies caller project identity; this
    # tests refusal against real persisted Sprint ownership, not file edits.
    monkeypatch.setattr(planner.runtime.sprint_tools.commands, 'project', 'foreign-project')
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='project|проект'):
        reopen(planner, published['revision'])
    assert unchanged(planner) == before


@pytest.mark.parametrize('field', ['pending', 'publication'])
def test_unresolved_external_effect_blocks_reopen_without_changes(planned, field):
    _, planner, published = planned
    # An explicit execution-owner fixture models an unknown external effect;
    # it does not manufacture any Task lifecycle/proof state with SQL.
    add_execution(planner, 'A', **{field: {'kind': 'unresolved-external-effect'}})
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='pending|external|publication|незаверш'):
        reopen(planner, published['revision'])
    assert unchanged(planner) == before


@pytest.mark.parametrize('missing', [True, False])
def test_incomplete_or_unknown_member_facts_never_grant_permission(planned, monkeypatch, missing):
    _, planner, published = planned
    with planner.runtime.store.unit_of_work() as unit:
        repository_type = type(unit.sprints)
    original = repository_type.facts

    def inconsistent(repository, sprint_id):
        facts = deepcopy(original(repository, sprint_id))
        if missing:
            facts.pop('A')
        else:
            facts['A']['status'] = 'unrecognised-state'
        return facts

    monkeypatch.setattr(repository_type, 'facts', inconsistent)
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='member|missing|state|участ|состоя'):
        reopen(planner, published['revision'])
    assert unchanged(planner) == before


def test_reopen_exact_replay_returns_saved_outcome_after_republication(planned):
    _, planner, published = planned
    packet = intent(published['revision'])
    original = invoke(planner, packet)
    assert original['status'] == 'draft'
    assert original['revision'] == published['revision'] + 1
    live = publish(planner, original['revision'], 'publish-again')
    assert live['status'] == 'planned'
    assert live['revision'] > original['revision']
    before = unchanged(planner)
    assert invoke(planner, packet) == original
    assert unchanged(planner) == before


@pytest.mark.parametrize('field', ['reason', 'authorization', 'expected_revision'])
def test_changed_request_identity_and_stale_new_request_are_atomic(planned, field):
    _, planner, published = planned
    packet = intent(published['revision'])
    assert invoke(planner, packet)['status'] == 'draft'
    before = unchanged(planner)
    changed = {**packet, field: 0 if field == 'expected_revision' else 'different'}
    with pytest.raises(PoiseError, match='Request|request|запрос'):
        invoke(planner, changed)
    assert unchanged(planner) == before
    with pytest.raises(PoiseError, match='revision|ревиз'):
        reopen(planner, published['revision'], request_id='stale-new')
    assert unchanged(planner) == before


def test_reopen_does_not_change_another_sprint(planned):
    project, planner, published = planned
    other = deepcopy(task(project, 'T-A'))
    other['sprint_id'] = 'T'
    planner.runtime.sprint_tools.apply({
        'action': 'draft', 'sprint_id': 'T', 'request_id': 'draft-T', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'},
        'changes': [
            {'kind': 'purpose', 'goal': 'Independent work', 'requirements': ['T-R'], 'definition_of_done': ['T-D']},
            {'kind': 'sections', 'values': {'plan': 'Independent Sprint.'}},
            {'kind': 'upsert_tasks', 'tasks': [other]},
            {'kind': 'dependencies', 'items': []},
        ],
    })
    planner.runtime.sprint_tools.apply({'action': 'publish', 'sprint_id': 'T', 'request_id': 'publish-T', 'expected_revision': 1})
    before = planner.runtime.sprint_tools.commands.read('T', 'plan')
    other_task = deepcopy(planner.runtime.task_queries.record('T-A'))
    assert reopen(planner, published['revision'])['status'] == 'draft'
    assert planner.runtime.sprint_tools.commands.read('T', 'plan') == before
    assert planner.runtime.task_queries.record('T-A') == other_task


def test_draft_blocks_execution_and_republication_restores_admission(planned):
    project, planner, published = planned
    opened = reopen(planner, published['revision'])
    worker = client(project, 'resumer')
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='eligible|draft|published|доступ|чернов'):
        bootstrap(worker, 'A')
    assert unchanged(planner) == before
    assert publish(planner, opened['revision'], 'publish-admission')['eligible'] == ['A']
    assert bootstrap(worker, 'A')['status'] == 'active'


def test_republish_retains_verified_and_completed_task_contracts(planned):
    project, planner, published = planned
    prepare_member(project, 'completed')
    worker = client(project, 'second-executor')
    context = bootstrap(worker, 'B')
    assert verify(worker, context)['status'] == 'verified'
    handoff(worker, 'B-verified')
    assert planner.runtime.task_queries.record('A')['status'] == 'completed'
    assert planner.runtime.task_queries.record('B')['status'] == 'verified'
    before = task_rows(planner)
    opened = reopen(planner, published['revision'])
    assert publish(planner, opened['revision'], 'republish-retained')['revision'] == opened['revision'] + 1
    assert task_rows(planner) == before


def test_started_successor_prerequisite_change_needs_authorized_restart(planned):
    project, planner, published = planned
    prepare_member(project, 'verified')
    opened = reopen(planner, published['revision'])
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='restart|started|contract|перезап|договор'):
        draft(planner, [], revision=opened['revision'], request_id='reverse-started', updates=[
            {'kind': 'dependencies', 'items': [{'predecessor': 'B', 'successor': 'A', 'kind': 'completion'}]},
        ])
    assert unchanged(planner) == before


def test_verified_restart_ready_and_republish_preserve_history(planned):
    project, planner, published = planned
    context = prepare_member(project, 'verified')
    prior = deepcopy(planner.runtime.task_queries.record('A'))
    opened = reopen(planner, published['revision'])
    owner = client(project, 'restart-owner')
    restarted = restart(owner, 'A', prior['version'], request_id='revise-A')
    assert restarted['status'] == 'newborn'
    revised_contract = deepcopy(prior['contract'])
    revised_contract['goal'] = 'Revised A capability.'
    revised_contract['requirements'] = ['Revised A prerequisite capability.']
    bind_task_requirements(revised_contract, project['requirements_registry'])
    patch = {key: revised_contract[key] for key in (
        'goal', 'requirements', 'requirements_snapshot', 'requirements_agreement',
    )}
    changed = owner.invoke(request('task', {
        'action': 'edit', 'task_id': 'A', 'request_id': 'edit-revised-A',
        'expected_revision': restarted['revision'], 'patch': patch, 'remove': [],
    }))
    ready = owner.invoke(request('task', {
        'action': 'ready', 'task_id': 'A', 'request_id': 'ready-revised-A',
        'expected_revision': changed['revision'],
    }))
    assert ready['status'] == 'newborn'
    assert ready['ready'] is True
    handoff(owner, 'readied-A')
    assert planner.runtime.task_queries.record('A')['status'] == 'newborn'
    republished = publish(planner, opened['revision'], 'republish-revised-A')
    assert republished['eligible'] == ['A']
    after = planner.runtime.task_queries.record('A')
    assert after['status'] == 'available'
    assert after['contract']['goal'] == 'Revised A capability.'
    assert after['contract']['requirements'] == ['Revised A prerequisite capability.']
    assert after['history'][:len(prior['history'])] == prior['history']
    assert Path(context['worktree']).exists()


def test_publication_conflict_preserves_concurrent_edit(planned, monkeypatch):
    _, planner, published = planned
    opened = reopen(planner, published['revision'])
    commands = planner.runtime.sprint_tools.commands
    original = commands.publication_preflight
    snapshots = []

    def race(packet, identity):
        prepared = original(packet, identity)
        draft(planner, [], revision=opened['revision'], request_id='concurrent-edit', updates=[
            {'kind': 'sections', 'values': {'plan': 'Concurrent plan must survive.'}},
        ])
        snapshots.append(unchanged(planner))
        return prepared

    monkeypatch.setattr(commands, 'publication_preflight', race)
    with pytest.raises(PoiseError, match='changed|revision|измен|ревиз'):
        publish(planner, opened['revision'], 'racing-publish')
    assert len(snapshots) == 1
    assert unchanged(planner) == snapshots[0]


def test_changed_graph_republication_reconciles_current_projection(planned):
    _, planner, published = planned
    opened = reopen(planner, published['revision'])
    edge = {'predecessor': 'B', 'successor': 'A', 'kind': 'completion'}
    revised = draft(planner, [], revision=opened['revision'], request_id='reverse-available', updates=[
        {'kind': 'dependencies', 'items': [edge]},
    ])
    assert publish(planner, revised['revision'], 'publish-new-graph')['eligible'] == ['B']
    assert sprint_rows(planner)['dependencies'] == [('S', 'B', 'A', 'completion')]
    history = planner.runtime.sprint_tools.commands.read('S', 'history')['layers']
    assert any(layer['data']['aggregate']['plan']['dependencies'] == [
        {'predecessor': 'A', 'successor': 'B', 'kind': 'completion'},
    ] for layer in history)


def test_disconnected_graph_cannot_be_republished(planned):
    _, planner, published = planned
    opened = reopen(planner, published['revision'])
    before_edit = unchanged(planner)
    try:
        revised = draft(planner, [], revision=opened['revision'], request_id='remove-real-edge', updates=[
            {'kind': 'dependencies', 'items': []},
        ])
    except PoiseError as failure:
        assert any(word in str(failure).lower() for word in ('connect', 'depend', 'graph', 'связ', 'граф'))
        assert unchanged(planner) == before_edit
        return  # Either lawful edit-time or publish-time graph validation.
    before = unchanged(planner)
    with pytest.raises(PoiseError, match='connect|depend|graph|связ|граф'):
        publish(planner, revised['revision'], 'publish-disconnected')
    assert unchanged(planner) == before


def test_fixture_guard_released_verification_has_real_proof(planned):
    project, planner, _ = planned
    context = prepare_member(project, 'verified')
    record = planner.runtime.task_queries.record('A')
    rows = task_rows(planner)
    assert record['status'] == 'verified'
    assert record['claimed_by'] is None
    assert Path(context['worktree']).exists()
    assert len(rows['submissions']) == 1
    assert len(rows['results']) == 1
    assert len(rows['evidence']) >= 1


def test_fixture_guard_pending_projection_is_independent(planned):
    _, planner, _ = planned
    add_execution(planner, 'A', pending={'kind': 'unresolved-external-effect'})
    with planner.runtime.store.transaction() as db:
        row = db.execute('SELECT data FROM task_execution WHERE task_id=?', ('A',)).fetchone()
    assert json.loads(row['data'])['pending'] == {'kind': 'unresolved-external-effect'}
    assert planner.runtime.task_queries.record('A')['status'] == 'available'
