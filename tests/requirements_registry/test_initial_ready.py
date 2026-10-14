"""Initial admission preserves validated lineage and real optional decisions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from conftest import WorkPoise
from poise.application.duplicate_reuse import reusable_contract
from poise.application.work import WorkTools
from poise.infrastructure.sqlite.transfers import SqliteTransferRepository
from poise.modules.foundation.errors import PoiseError
from poise.modules.requirements_registry.service import TaskRequirementsGate
from sprints.helpers import draft, publish, setup, task as sprint_task
from tasks.test_newborn_lifecycle import create, edit, task_action, tools


DATA = json.loads((Path(__file__).parent / 'fixtures/initial_ready_contract.json').read_text())


def contract(project, mode):
    body = deepcopy(project['task'])
    body['requirements'] = deepcopy(DATA['requirements'])
    body['requirements_snapshot'] = deepcopy(DATA['snapshot'])
    if mode == 'approved':
        body['requirements_agreement'] = deepcopy(DATA['approval'])
    elif mode == 'null':
        body['requirements_agreement'] = None
    else:
        del body['requirements_agreement']
    return body


def prepare_newborn(project, mode, identifier='T1'):
    client = tools(project, 'admission-owner')
    born = create(client, identifier)
    patch = contract(project, mode)
    patch['id'] = identifier
    filled = edit(client, identifier, born['revision'], patch, 'fill-ready-contract')
    return client, filled


def ready(client, revision, request_id='ready-contract'):
    return task_action(client, action='ready', task_id='T1',
                       expected_revision=revision, request_id=request_id)


def observation(runtime, app):
    with runtime.store.unit_of_work() as unit:
        database = unit.tasks.db
        rows = tuple(tuple(row) for row in database.execute(
            'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
            'FROM tasks ORDER BY id'))
        journal = tuple(tuple(row) for row in database.execute(
            'SELECT seq,at,session_id,task_id,event,data FROM journal ORDER BY seq'))
    paths = tuple(sorted(path.relative_to(app).as_posix() for path in app.rglob('*')))
    files = tuple(sorted((path.relative_to(app).as_posix(), path.read_bytes())
                         for path in app.rglob('*') if path.is_file()))
    return rows, journal, paths, files


def stored_context(runtime):
    with runtime.store.unit_of_work() as unit:
        row = unit.tasks.db.execute('SELECT metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
    metadata = json.loads(row[0])
    return metadata['requirements_snapshot'], metadata['requirements_agreement']


@pytest.mark.parametrize('mode', ['absent', 'null', 'approved'])
def test_real_newborn_ready_preserves_the_actual_decision(project, mode):
    client, filled = prepare_newborn(project, mode)
    result = ready(client, filled['revision'])
    assert result['status'] == 'available'
    assert result['ready'] is True
    assert result['claimed_by'] is None
    snapshot, approval = stored_context(client.runtime)
    assert snapshot == DATA['snapshot']
    assert approval == (DATA['approval'] if mode == 'approved' else None)


@pytest.mark.parametrize('mode', ['absent', 'null'])
def test_initial_gate_preserves_absence_without_mutating_input(project, mode):
    body = contract(project, mode)
    original = deepcopy(body)
    gate = TaskRequirementsGate.enabled(lambda: project['requirements_registry'])
    prepared, context = gate.prepare_contract(body)
    assert body == original
    assert 'requirements_snapshot' not in prepared
    assert 'requirements_agreement' not in prepared
    assert context['snapshot'] == DATA['snapshot']
    assert context['agreement'] is None


def test_explicit_approval_publication_remains_strict_and_immutable(project):
    client = tools(project, 'publisher-owner')
    create(client, 'PUBLISH')
    runtime = client.runtime
    before = observation(runtime, project['app'])
    with pytest.raises(PoiseError, match='agreement'):
        runtime.task_commands.publish_requirements('PUBLISH', DATA['snapshot']['task_requirements'], None)
    assert observation(runtime, project['app']) == before
    published = runtime.task_commands.publish_requirements(
        'PUBLISH', DATA['snapshot']['task_requirements'], DATA['approval'])
    assert published == DATA['snapshot']
    with runtime.store.unit_of_work() as unit:
        row = unit.tasks.db.execute('SELECT metadata FROM tasks WHERE id=?', ('PUBLISH',)).fetchone()
    metadata = json.loads(row[0])
    assert metadata['requirements_snapshot'] == DATA['snapshot']
    assert metadata['requirements_agreement'] == DATA['approval']
    before = observation(runtime, project['app'])
    with pytest.raises(PoiseError, match='immutable'):
        runtime.task_commands.publish_requirements(
            'PUBLISH', DATA['snapshot']['task_requirements'], DATA['approval'])
    assert observation(runtime, project['app']) == before


@pytest.mark.parametrize('case', DATA['invalid_approvals'], ids=lambda case: case['id'])
def test_provided_invalid_decision_preserves_rows_journal_and_files(project, case):
    client, filled = prepare_newborn(project, 'approved')
    value = deepcopy(case['value']) if 'value' in case else deepcopy(DATA['approval'])
    if 'remove' in case:
        del value[case['remove']]
    if 'patch' in case:
        value.update(case['patch'])
    changed = edit(client, 'T1', filled['revision'],
                   {'requirements_agreement': value}, 'invalid-decision')
    (project['app'] / 'foreign-note.txt').write_bytes(b'preserve these foreign bytes\n')
    before = observation(client.runtime, project['app'])
    with pytest.raises(PoiseError, match='agreement'):
        ready(client, changed['revision'])
    assert observation(client.runtime, project['app']) == before


@pytest.mark.parametrize('case', ['missing', 'null', 'stale', 'text-mismatch', 'gap'])
def test_invalid_snapshot_preserves_rows_journal_and_files(project, case):
    client, filled = prepare_newborn(project, 'approved', 'OTHER')
    seeded = task_action(client, action='ready', task_id='OTHER',
                         expected_revision=filled['revision'], request_id='ready-other-contract')
    assert seeded['status'] == 'available'
    runtime = WorkPoise(project['config_path'], 'snapshot-owner')
    body = contract(project, 'approved')
    if case == 'missing':
        del body['requirements_snapshot']
    elif case == 'null':
        body['requirements_snapshot'] = None
    elif case == 'stale':
        body['requirements_snapshot']['requirements']['FIXTURE-SYSTEM']['text'] = 'Stale text.'
    elif case == 'text-mismatch':
        body['requirements'] = ['A different Task requirement.']
    else:
        body['requirements_snapshot']['task_requirements'][0]['applications'] = []
    (project['app'] / 'foreign-note.txt').write_bytes(b'preserve these foreign bytes\n')
    before = observation(runtime, project['app'])
    with pytest.raises(PoiseError, match='snapshot'):
        runtime.bootstrap(body)
    assert observation(runtime, project['app']) == before


@pytest.mark.parametrize('mode', ['absent', 'null'])
@pytest.mark.parametrize('case', ['missing', 'null', 'stale', 'text-mismatch', 'gap'])
def test_absent_decision_does_not_bypass_snapshot_validation(project, mode, case):
    body = contract(project, mode)
    if case == 'missing':
        del body['requirements_snapshot']
    elif case == 'null':
        body['requirements_snapshot'] = None
    elif case == 'stale':
        body['requirements_snapshot']['requirements']['FIXTURE-SYSTEM']['text'] = 'Stale text.'
    elif case == 'text-mismatch':
        body['requirements'] = ['A different Task requirement.']
    else:
        body['requirements_snapshot']['task_requirements'][0]['applications'] = []
    original = deepcopy(body)
    gate = TaskRequirementsGate.enabled(lambda: project['requirements_registry'])
    with pytest.raises(PoiseError, match='snapshot'):
        gate.prepare_contract(body)
    assert body == original


@pytest.mark.parametrize('mode', ['absent', 'approved'])
def test_saved_context_replay_uses_the_exact_persisted_facts(project, mode):
    body = contract(project, mode)
    approval = None if mode == 'absent' else DATA['approval']
    body['requirements_agreement'] = deepcopy(approval)

    def no_live_registry():
        raise AssertionError('Historical context must not observe the live registry')

    gate = TaskRequirementsGate.enabled(no_live_registry)
    prepared, context = gate.prepare_restarted_contract(body, DATA['snapshot'], approval)
    assert prepared['requirements'] == DATA['requirements']
    assert 'requirements_agreement' not in prepared
    assert context['snapshot'] == DATA['snapshot']
    assert context['agreement'] == approval


@pytest.mark.parametrize('mode', ['absent', 'approved'])
def test_changed_persisted_decision_is_rejected(project, mode):
    body = contract(project, mode)
    approval = None if mode == 'absent' else DATA['approval']
    body['requirements_agreement'] = deepcopy(DATA['approval']) if mode == 'absent' else None
    original = deepcopy(body)
    gate = TaskRequirementsGate.enabled(lambda: project['requirements_registry'])
    with pytest.raises(PoiseError, match='differs from persisted state'):
        gate.prepare_restarted_contract(body, DATA['snapshot'], approval)
    assert body == original


@pytest.mark.parametrize('mode', ['absent', 'approved'])
def test_real_restart_ready_preserves_the_exact_context(project, mode):
    client, filled = prepare_newborn(project, mode)
    first = ready(client, filled['revision'])
    restarted = task_action(client, action='restart', task_id='T1',
                            expected_version=first['revision'], request_id='restart-contract',
                            reason='Recheck the unchanged saved contract.',
                            authorization='The user authorized this fixture restart.')
    result = ready(client, restarted['revision'], 'ready-restarted-contract')
    assert result['status'] == 'available'
    snapshot, approval = stored_context(client.runtime)
    assert snapshot == DATA['snapshot']
    assert approval == (None if mode == 'absent' else DATA['approval'])


def test_authorized_revision_validates_new_context_with_no_prior_decision(project, monkeypatch):
    other, other_filled = prepare_newborn(project, 'approved', 'OTHER')
    task_action(other, action='ready', task_id='OTHER',
                expected_revision=other_filled['revision'], request_id='ready-other-revision')
    client, filled = prepare_newborn(project, 'approved')
    first = ready(client, filled['revision'])
    # Test-only arrangement of a saved optional fact, independent of initial admission.
    # No real installation database or recorded historical journal entry is changed.
    with client.runtime.store.unit_of_work() as unit:
        unit.tasks.db.execute(
            "UPDATE tasks SET metadata=json_set(metadata,'$.requirements_agreement',json('null')) "
            "WHERE id=?", ('T1',))
    assert stored_context(client.runtime) == (DATA['snapshot'], None)
    sentinel = project['app'] / 'foreign-note.txt'
    sentinel.write_bytes(b'preserve these foreign bytes\n')
    before_rows, before_journal, _, _ = observation(client.runtime, project['app'])
    restarted = task_action(client, action='restart', task_id='T1',
                            expected_version=first['revision'], request_id='restart-revision',
                            reason='Revise the fixture requirement.',
                            authorization='The user authorized this fixture restart.')
    revised = contract(project, 'null')
    revised['requirements'] = ['double(n) returns n+n']
    revised['requirements_snapshot']['task_requirements'] = [{
        'text': 'double(n) returns n+n', 'applications': ['FIXTURE-APPLICATION']}]
    changed = edit(client, 'T1', restarted['revision'], revised, 'edit-revised-context')
    calls = []
    original = TaskRequirementsGate.prepare_contract

    def observe(gate, body):
        calls.append((deepcopy(body['requirements_snapshot']), body['requirements_agreement']))
        return original(gate, body)

    monkeypatch.setattr(TaskRequirementsGate, 'prepare_contract', observe)
    try:
        result = ready(client, changed['revision'], 'ready-revised-context')
    finally:
        assert calls == [(revised['requirements_snapshot'], None)]
    assert result['status'] == 'available'
    assert stored_context(client.runtime) == (revised['requirements_snapshot'], None)
    with client.runtime.store.unit_of_work() as unit:
        row = unit.tasks.db.execute('SELECT metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
    history = json.loads(row[0])['restart_history'][-1]
    assert history['before_requirements_snapshot'] == DATA['snapshot']
    assert history['before_requirements_agreement'] is None
    after_rows, after_journal, _, _ = observation(client.runtime, project['app'])
    assert [row for row in after_rows if row[0] == 'OTHER'] == [
        row for row in before_rows if row[0] == 'OTHER']
    assert after_journal[:len(before_journal)] == before_journal
    assert sentinel.read_bytes() == b'preserve these foreign bytes\n'


@pytest.mark.parametrize('mode', ['absent', 'approved'])
def test_duplicate_and_reuse_owners_preserve_optional_facts(project, mode):
    setup(project)
    project['task'] = sprint_task(project, 'T1')
    project['task']['sprint_id'] = None
    client, filled = prepare_newborn(project, mode)
    ready(client, filled['revision'])
    draft(client, [])
    duplicate = task_action(client, action='duplicate', task_id='CHILD', parent_id='T1',
                            sprint_id='S', request_id='duplicate-contract')
    assert duplicate['status'] == 'newborn'
    assert duplicate['draft']['requirements_snapshot'] == DATA['snapshot']
    if mode == 'absent':
        assert 'requirements_agreement' not in duplicate['draft']
    else:
        assert duplicate['draft']['requirements_agreement'] == DATA['approval']
    with client.runtime.store.unit_of_work() as unit:
        payload = reusable_contract(unit.tasks.load('T1'), unit.tasks.restart_context('T1'))
    assert payload['requirements_snapshot'] == DATA['snapshot']
    assert payload['requirements_agreement'] == (None if mode == 'absent' else DATA['approval'])


def test_invalid_sprint_member_preserves_the_complete_prepublication_state(project):
    setup(project)
    a, b = sprint_task(project, 'A'), sprint_task(project, 'B')
    b['requirements_snapshot']['requirements']['FIXTURE-SYSTEM']['text'] = 'Stale text.'
    runtime = WorkPoise(project['config_path'], 'sprint-owner')
    client = WorkTools(runtime)
    planned = draft(client, [a, b])
    before = observation(runtime, project['app'])
    with pytest.raises(PoiseError, match='snapshot'):
        publish(client, planned['revision'])
    assert observation(runtime, project['app']) == before


@pytest.mark.parametrize('mode', ['absent', 'approved'])
def test_real_transfer_snapshot_preserves_optional_facts(project, tmp_path, mode):
    client, filled = prepare_newborn(project, mode)
    ready(client, filled['revision'])
    owner = SqliteTransferRepository(client.runtime.store.database)
    captured = owner.capture(['T1'], None, client.runtime.cfg['project'], 1)
    snapshot = tmp_path / 'transfer.sqlite'
    owner.write_snapshot(snapshot, captured)
    restored = owner.read_snapshot(snapshot)
    assert len(restored['tasks']) == 1
    metadata = json.loads(restored['tasks'][0]['metadata'])
    assert metadata['requirements_snapshot'] == DATA['snapshot']
    assert metadata['requirements_agreement'] == (None if mode == 'absent' else DATA['approval'])
