"""Task-local planning via the real public work boundary, not SQL lifecycle edits."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import request, configure
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from tasks.test_newborn_lifecycle import create, edit, task_action, complete_patch
from runtime_services.test_task_restart import restart


POLICY = {
    'reviewer': ['goal', 'stage_contracts', 'process', 'requirements',
                 'requirements_snapshot', 'requirements_agreement'],
    'user': ['goal', 'goal_type', 'stage_contracts', 'process', 'requirements',
             'requirements_snapshot', 'requirements_agreement', 'planning',
             'methods', 'method_inputs', 'checks', 'definition_of_done',
             'artifact_requirements', 'content_contract', 'evidence_plan',
             'decomposition', 'executable_obligations'],
}


def client(project):
    configure(project)
    return WorkTools(WorkPoise(project['config_path'], 'planner'))


def full_draft(project, task_id='FLEX'):
    return {**complete_patch(project, task_id), 'goal_type': 'development',
            'planning': {'schema': 'task-planning-1', 'template': None,
                         'restart_revision_policy': deepcopy(POLICY)}}


def ready(c, task_id, revision, request_id='ready-flex'):
    return task_action(c, action='ready', task_id=task_id, expected_revision=revision,
                       request_id=request_id)


def test_local_process_can_be_reshaped_and_survives_reselection(project):
    c = client(project)
    create(c, 'FLEX')
    changed = edit(c, 'FLEX', 0, {**full_draft(project), 'process_changes': [
        {'op': 'patch_stage', 'id': 'implementation', 'set': {'allowed_paths': ['examples/**']}}
    ]}, 'reshape')
    process = changed['process']
    assert process['stages'][2]['allowed_paths'] == ['examples/**']
    original = json.loads((project['config_path'].parent/'config/processes/development.json').read_text())
    assert original['stages'][2]['allowed_paths'] == ['src/**']
    same = edit(c, 'FLEX', changed['revision'], {'goal_type': 'development'}, 'same-type')
    assert same['process'] == process
    assert c.runtime.task_queries.record('FLEX')['process'] == process


def test_explicit_process_needs_no_global_goal_registration(project):
    c = client(project)
    create(c, 'LOCAL')
    process = deepcopy(project['process'])
    process['goal_type'] = 'one_off_analysis'
    draft = full_draft(project, 'LOCAL')
    draft['goal_type'] = 'one_off_analysis'
    out = edit(c, 'LOCAL', 0, {**draft, 'process': process}, 'custom-process')
    assert ready(c, 'LOCAL', out['revision'])['status'] == 'available'
    assert c.runtime.task_queries.record('LOCAL')['process']['goal_type'] == 'one_off_analysis'


def test_incomplete_flexible_draft_may_remove_fields_but_not_be_ready(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    out = edit(c, 'FLEX', out['revision'], {}, 'remove-goal', remove=['goal'])
    assert 'goal' not in out['draft']
    with pytest.raises(PoiseError):
        ready(c, 'FLEX', out['revision'])
    assert c.runtime.task_queries.record('FLEX')['status'] == 'newborn'


def test_frozen_contract_rejects_ordinary_edit(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    ready(c, 'FLEX', out['revision'])
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError):
        edit(c, 'FLEX', before['version'], {'goal': 'Other work'}, 'late-edit')
    assert c.runtime.task_queries.record('FLEX') == before


def test_invalid_local_graph_does_not_change_draft(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError):
        edit(c, 'FLEX', out['revision'], {'process_changes': [
            {'op': 'remove_stage', 'id': 'implementation'}]}, 'bad-graph')
    assert c.runtime.task_queries.record('FLEX') == before


def test_newborn_can_restart_without_inventing_a_completed_route(project):
    c = client(project)
    born = create(c, 'UNPLANNED')
    out = restart(c, 'UNPLANNED', born['revision'])
    assert out['status'] == 'newborn'
    assert out['revision'] == 1
    assert out['goal_type'] is None
    assert out['draft'] == {}
    assert restart(c, 'UNPLANNED', born['revision'])['replayed'] is True


def test_restart_requires_explicit_decision_for_flexible_contract(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    with pytest.raises(PoiseError, match='decision|authorization'):
        restart(c, 'FLEX', promoted['revision'], authorization='Executor chooses to restart')
    assert c.runtime.task_queries.record('FLEX')['status'] == 'available'


def test_restart_reviewer_may_change_only_saved_fields(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    out = restart(c, 'FLEX', promoted['revision'], authorization={
        'role': 'reviewer', 'decision': 'Review permits a revised goal.'})
    revised = edit(c, 'FLEX', out['revision'], {'goal': 'A corrected local goal'}, 'allowed')
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError, match='revision policy'):
        edit(c, 'FLEX', revised['revision'], {'definition_of_done': ['Bypass verification']}, 'denied')
    assert c.runtime.task_queries.record('FLEX') == before
    ready(c, 'FLEX', revised['revision'], 'ready-revised')
    rec = c.runtime.task_queries.record('FLEX')
    assert rec['contract']['goal'] == 'A corrected local goal'
    with c.runtime.store.unit_of_work() as uow:
        history = uow.tasks.restart_context('FLEX')['restart_history']
    assert history[-1]['planning_revision']['before_contract']['goal'] == project['task']['goal']
    assert history[-1]['resolved_revision']['changed_fields'] == ['goal']


def test_reviewer_can_explicitly_agree_broader_task_revision(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    agreed = {
        'role': 'reviewer',
        'decision': 'The independent reviewer agrees to revise acceptance.',
        'revision_fields': ['definition_of_done'],
    }
    out = restart(c, 'FLEX', promoted['revision'], authorization=agreed)
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError, match='reviewer authorization'):
        edit(c, 'FLEX', out['revision'], {'goal': 'Unapproved new goal'}, 'denied-goal')
    assert c.runtime.task_queries.record('FLEX') == before
    changed = edit(c, 'FLEX', out['revision'], {
        'definition_of_done': ['The corrected behavior is verified.'],
    }, 'agreed-dod')
    assert ready(c, 'FLEX', changed['revision'], 'agreed-ready')['status'] == 'available'
    with c.runtime.store.unit_of_work() as uow:
        history = uow.tasks.restart_context('FLEX')['restart_history']
    assert history[-1]['planning_revision']['authorization'] == agreed
    assert history[-1]['planning_revision']['allowed_changes'] == ['definition_of_done']
    assert history[-1]['resolved_revision']['changed_fields'] == ['definition_of_done']


@pytest.mark.parametrize('role, fields', [
    ('reviewer', []),
    ('reviewer', ['unknown_field']),
    ('reviewer', ['goal', 'goal']),
    ('reviewer', ['id']),
    ('reviewer', 'definition_of_done'),
    ('user', ['definition_of_done']),
])
def test_reviewer_revision_fields_reject_invalid_grants(project, role, fields):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError, match='revision_fields'):
        restart(c, 'FLEX', promoted['revision'], authorization={
            'role': role, 'decision': 'Review agrees', 'revision_fields': fields,
        })
    assert c.runtime.task_queries.record('FLEX') == before


def test_restart_can_reagree_changed_requirements_through_live_registry(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    out = restart(c, 'FLEX', promoted['revision'], authorization={
        'role': 'user', 'decision': 'The user approved the revised requirement.'})
    contract = deepcopy(project['task'])
    contract['requirements'] = ['The corrected implementation meets its exact local contract.']
    from conftest import bind_task_requirements
    registry = c.runtime.requirements_commands.store.registry()
    bind_task_requirements(contract, registry)
    patch = {key:contract[key] for key in ('requirements', 'requirements_snapshot', 'requirements_agreement')}
    revised = edit(c, 'FLEX', out['revision'], patch, 'reagree')
    assert ready(c, 'FLEX', revised['revision'], 'ready-reagreed')['status'] == 'available'
    assert c.runtime.task_queries.record('FLEX')['contract']['requirements'] == contract['requirements']


def test_future_output_uses_task_scope_not_template_scope(project):
    c = client(project)
    create(c, 'FLEX')
    draft = full_draft(project)
    # The test fixture's only future source is tests/test_double.py produced at tests.
    draft['stage_contracts'][0]['allowed_paths'] = ['tests/**']
    process = deepcopy(project['process'])
    process['stages'][0]['allowed_paths'] = ['other-tests/**']
    out = edit(c, 'FLEX', 0, {**draft, 'process': process}, 'scope')
    assert ready(c, 'FLEX', out['revision'])['status'] == 'available'


def planning_catalogue(project):
    """Test-owned blueprint and provider settings; no installer/model creates expected data."""
    import hashlib
    root = project['config_path'].parent
    blueprint = {'schema': 'task-blueprint-1', 'goal_type': 'development',
                 'parameters': {'identity': 'text', 'membership': 'nullable_text', 'goal': 'text'},
                 'task': {**deepcopy(project['task']), 'id': {'$input': 'identity'},
                          'sprint_id': {'$input': 'membership'}, 'goal': {'$input': 'goal'}}}
    path = write_json(root/'templates/development.json', blueprint)
    value = json.dumps(blueprint, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.sha256(value.encode()).hexdigest()
    write_json(root/'editor.json', {
        'schema': 'goal-config-editor-1', 'root': '.', 'database': 'editor.sqlite',
        'lock': 'editor.lock', 'responses': 'responses', 'file_mode': 384, 'json_indent': 2,
        'lock_seconds': 2.0, 'lock_poll_seconds': 0.01, 'max_changes': 1000,
        'max_input_bytes': 1048576, 'output_chars': 8192,
        'exit_codes': {'success': 0, 'rejected': 2, 'pending': 3},
        'processes': {'development': 'config/processes/development.json'}, 'templates': {},
    })
    settings = write_json(root/'catalogue.json', {
        'schema': 'process-catalogue-1', 'editor': 'editor.json', 'max_items': 1000,
        'task_templates': {'dev-test': {'path': 'templates/development.json', 'version': '1',
                                     'digest': digest, 'goal_type': 'development'}},
    })
    project['cfg']['task_planning'] = {'catalogue': str(settings),
                                      'restart_revision_policy': deepcopy(POLICY)}
    return path


def test_goal_type_materializes_editable_task_in_one_public_request(project):
    template = planning_catalogue(project)
    c = client(project)
    packet = {'action': 'create', 'task_id': 'STARTER', 'sprint_id': None,
              'request_id': 'create-starter', 'goal_type': 'development',
              'patch': {'goal': 'Actual maintenance, not template prose'}}
    out = task_action(c, **packet)
    assert out['status'] == 'newborn'
    assert out['draft']['goal'] == 'Actual maintenance, not template prose'
    assert out['draft']['planning']['template']['id'] == 'dev-test'
    assert out['draft']['planning']['restart_revision_policy'] == POLICY
    assert out['process']['stages'][0]['id'] == 'tests'
    assert out['draft']['definition_of_done'] == project['task']['definition_of_done']
    template.unlink()  # The already materialized task must not consult this source again.
    assert task_action(c, **packet) == out
    out = edit(c, 'STARTER', out['revision'], {'goal_type': 'development'}, 'reselect')
    assert ready(c, 'STARTER', out['revision'])['status'] == 'available'
    restarted_runtime = WorkPoise(project['config_path'], 'executor')
    ctx = restarted_runtime.bootstrap({'id': 'STARTER'})
    assert ctx['status'] == 'active'


def test_materialization_cannot_replace_the_requested_identity(project):
    planning_catalogue(project)
    c = client(project)
    with pytest.raises(PoiseError, match='identity'):
        task_action(c, action='create', task_id='ORIGINAL', sprint_id=None,
                    request_id='wrong-id', goal_type='development', patch={'id': 'INJECTED'})
    assert c.runtime.task_queries.record('ORIGINAL') is None
    assert c.runtime.task_queries.record('INJECTED') is None


@pytest.mark.parametrize('patch', [
    {'goal_type': []}, {'goal_type': None}, {'goal_type': ''},
    {'goal_type': True}, {'process': None}, {'process_changes': None},
])
def test_invalid_explicit_planning_input_is_an_atomic_domain_error(project, patch):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError):
        edit(c, 'FLEX', out['revision'], patch, 'bad-value')
    assert c.runtime.task_queries.record('FLEX') == before


def test_repeated_restart_can_record_a_broader_reviewer_agreement(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    out = restart(c, 'FLEX', promoted['revision'], request_id='reviewer-restart',
                  authorization={'role': 'reviewer', 'decision': 'Revise local goal only.'})
    with pytest.raises(PoiseError, match='revision policy'):
        edit(c, 'FLEX', out['revision'], {'definition_of_done': ['Revised acceptance']}, 'denied-dod')
    out = restart(c, 'FLEX', out['revision'], request_id='reviewer-agreed-restart',
                  authorization={
                      'role': 'reviewer',
                      'decision': 'Reviewer agrees to revise acceptance.',
                      'revision_fields': ['definition_of_done'],
                  })
    out = edit(c, 'FLEX', out['revision'], {'definition_of_done': ['Revised acceptance']}, 'agreed-dod')
    assert ready(c, 'FLEX', out['revision'], 'agreed-ready')['status'] == 'available'
    with c.runtime.store.unit_of_work() as uow:
        history = uow.tasks.restart_context('FLEX')['restart_history']
    assert history[-1]['planning_revision']['authorization']['role'] == 'reviewer'
    assert history[-1]['planning_revision']['allowed_changes'] == ['definition_of_done']
    assert history[-1]['planning_revision']['before_contract']['definition_of_done'] == project['task']['definition_of_done']


def test_ready_sprint_newborn_is_already_frozen_before_publication(project):
    from sprints.helpers import setup
    setup(project)
    from sprints.helpers import changes, task as sprint_task
    c = client(project)
    c.invoke(request('sprint', {'action': 'draft', 'sprint_id': 'FLEXSPRINT',
        'request_id': 'draft-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': changes([])}))
    create(c, 'FLEX', sprint_id='FLEXSPRINT')
    draft = {**sprint_task(project, 'FLEX'), 'planning': full_draft(project)['planning']}
    draft['sprint_id'] = 'FLEXSPRINT'
    out = edit(c, 'FLEX', 0, draft, 'complete')
    promoted = ready(c, 'FLEX', out['revision'])
    assert promoted['ready'] is True
    assert promoted['status'] == 'newborn'
    with pytest.raises(PoiseError, match='authorization|decision'):
        restart(c, 'FLEX', promoted['revision'], request_id='bare-restart', authorization='executor request')
    out = restart(c, 'FLEX', promoted['revision'], request_id='review-restart',
                  authorization={'role': 'reviewer', 'decision': 'Review changes goal.'})
    with pytest.raises(PoiseError, match='revision policy'):
        edit(c, 'FLEX', out['revision'], {'definition_of_done': ['Unapproved acceptance']}, 'denied')


def test_executor_cannot_declare_itself_the_independent_restart_reviewer(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    ready(c, 'FLEX', out['revision'])
    c.runtime.bootstrap({'id': 'FLEX'})
    rec = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError, match='distinct reviewer'):
        restart(c, 'FLEX', rec['version'], authorization={
            'role': 'reviewer',
            'decision': 'I accept my own changed constraints.',
            'revision_fields': ['definition_of_done'],
        })
    assert c.runtime.task_queries.record('FLEX') == rec


def test_allocated_workspace_is_preserved_not_silently_replaced(project):
    c = client(project)
    create(c, 'FLEX')
    out = edit(c, 'FLEX', 0, full_draft(project), 'complete')
    ready(c, 'FLEX', out['revision'])
    c.runtime.bootstrap({'id': 'FLEX'})
    rec = c.runtime.task_queries.record('FLEX')
    with c.runtime.store.unit_of_work() as uow:
        workspace = uow.execution.load('FLEX')[0]['worktree']
    assert workspace is not None
    wip = Path(workspace) / 'local-uncommitted.txt'
    wip.write_text('Preserve this uncommitted work.\n')
    out = restart(c, 'FLEX', rec['version'], authorization={
        'role': 'user', 'decision': 'Revise the task while preserving its existing work.'})
    before = c.runtime.task_queries.record('FLEX')
    with pytest.raises(PoiseError, match='workspace'):
        edit(c, 'FLEX', out['revision'], {'process_changes': [
            {'op': 'set_worktree_required', 'value': False}]}, 'replace-workspace')
    assert c.runtime.task_queries.record('FLEX') == before
    assert wip.read_text() == 'Preserve this uncommitted work.\n'


def test_registered_goal_materializes_without_global_project_route(project):
    project['cfg']['task_planning'] = {
        'catalogue': str(Path(__file__).resolve().parents[2] / 'config/catalogue/settings.json'),
        'restart_revision_policy': deepcopy(POLICY)}
    c = client(project)
    out = task_action(c, action='create', task_id='TEST-WORK', sprint_id=None,
        request_id='test-starter', goal_type='test_development', patch={})
    assert out['draft']['goal_type'] == 'test_development'
    assert out['draft']['planning']['template']['id'] == 'test_development-v1'
    assert out['process']['goal_type'] == 'test_development'
    assert 'test_development' not in c.runtime.processes
    assert out['status'] == 'newborn'


def test_revised_ready_sprint_draft_keeps_resolved_revision_audit(project):
    from sprints.helpers import setup, changes, task as sprint_task
    setup(project)
    c = client(project)
    c.invoke(request('sprint', {
        'action': 'draft', 'sprint_id': 'FLEXSPRINT', 'request_id': 'draft-sprint',
        'expected_revision': None, 'template': {'id': 'basic', 'version': '1'},
        'changes': changes([]),
    }))
    create(c, 'FLEX', sprint_id='FLEXSPRINT')
    draft = {**sprint_task(project, 'FLEX'), 'sprint_id': 'FLEXSPRINT',
             'planning': full_draft(project)['planning']}
    out = edit(c, 'FLEX', 0, draft, 'complete')
    out = ready(c, 'FLEX', out['revision'])
    out = restart(c, 'FLEX', out['revision'], authorization={
        'role': 'reviewer', 'decision': 'The reviewer approves correcting the goal.'})
    out = edit(c, 'FLEX', out['revision'], {'goal': 'Corrected sprint-local goal'}, 'correct-goal')
    out = ready(c, 'FLEX', out['revision'], 'ready-corrected')
    assert out['ready'] is True
    assert out['status'] == 'newborn'
    with c.runtime.store.unit_of_work() as uow:
        history = uow.tasks.restart_context('FLEX')['restart_history']
    assert history[-1]['resolved_revision']['changed_fields'] == ['goal']
    assert len(history[-1]['resolved_revision']['contract_digest']) == 64
    assert history[-1]['planning_revision']['before_contract']['goal'] == draft['goal']
