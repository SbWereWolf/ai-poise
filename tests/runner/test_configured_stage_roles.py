"""V1-ROLE: process stages own roles; handlers do not choose participants."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from poise.application.tasks import require_reviewer_identity_in
from poise.modules.foundation.errors import DomainError
from poise.modules.goal_config.domain import GoalTypeDefinition
from poise.modules.tasks.domain import TaskStatus
from poise.modules.tasks.progression import progression_step
from poise.modules.workflow.domain import RouteDefinition


def process(producer_role='executor', review_role='executor'):
    return {
        'goal_type': 'role-contract', 'worktree_required': False,
        'route': {'entry': 'build'},
        'benefit': {'git_categories': [], 'sections': ['report']},
        'content_contract': {'sections': [], 'routes': [], 'requirements': []},
        'stages': [
            {'id': 'build', 'role': producer_role, 'handler': 'produce',
             'transitions': {'complete': 'inspect'}, 'rework_targets': ['build'],
             'read_only': False, 'allowed_paths': ['src/**'],
             'instruction': 'Produce the result.', 'normalization': 'strip',
             'sections': {'report': 'Result'}, 'required_sections': ['report'],
             'artifact_requirements': []},
            {'id': 'inspect', 'role': review_role, 'handler': 'inspect',
             'transitions': {'clear': None, 'changes_requested': 'build'},
             'rework_targets': ['build'], 'read_only': True, 'allowed_paths': [],
             'instruction': 'Inspect the result.', 'normalization': 'strip',
             'sections': {'report': 'Review'}, 'required_sections': ['report'],
             'artifact_requirements': []},
        ],
    }


def task_for(definition, *, current='build', status=TaskStatus.VERIFIED, outcome='complete'):
    return SimpleNamespace(
        route=RouteDefinition.from_process(definition),
        stage=SimpleNamespace(stage_id=current),
        state=SimpleNamespace(task_id='T-role', status=status, claimed_by='session-A'),
        progress=SimpleNamespace(outcome=outcome),
    )


def uow_for(actor='session-A', source_stage='build'):
    # Stub only the persistence port; the policy under test is the real owner.
    identity = {'executor_actor': actor, 'source': 'verified_task_event',
                'stage': source_stage, 'iteration': 1, 'submission_id': 17}
    return SimpleNamespace(tasks=SimpleNamespace(review_identity=lambda task_id: deepcopy(identity)))


def test_process_accepts_and_preserves_explicit_custom_stage_roles():
    raw = process('developer', 'assayer')
    parsed = GoalTypeDefinition.parse(raw)
    assert parsed.data == raw
    route = RouteDefinition.from_process(parsed.data)
    assert route.node('build').role == 'developer'
    assert route.node('inspect').role == 'assayer'


@pytest.mark.parametrize('bad_role', [None, '', '   ', 1, [], {}])
def test_invalid_role_is_rejected_instead_of_inferred_from_handler(bad_role):
    with pytest.raises(DomainError, match='role'):
        GoalTypeDefinition.parse(process(bad_role))


def test_missing_role_is_a_config_error_not_implicit_executor():
    raw = process()
    for stage in raw['stages']:
        del stage['role']
    with pytest.raises(DomainError, match='role'):
        GoalTypeDefinition.parse(raw)


def test_same_role_produce_to_inspect_advances_without_handoff():
    task = task_for(process('executor', 'executor'))
    decision = progression_step(task, 'inspect', False)
    assert decision.kind == 'advance'
    assert decision.from_role == decision.to_role == 'executor'


def test_same_handler_different_roles_requires_handoff():
    raw = process('writer', 'auditor')
    raw['stages'][1].update(handler='produce', transitions={'complete': None})
    decision = progression_step(task_for(raw), 'inspect', False)
    assert decision.kind == 'role_handoff_required'
    assert (decision.from_role, decision.to_role) == ('writer', 'auditor')


def test_role_boundary_resumes_after_confirmed_handoff():
    task = task_for(process('writer', 'auditor'))
    assert progression_step(task, 'inspect', True).kind == 'advance'


@pytest.mark.parametrize('producer_actor', ['session-A', 'session-B', None])
def test_same_role_does_not_require_actor_difference_or_actor_provenance(producer_actor):
    task = task_for(process('executor', 'executor'))
    assert require_reviewer_identity_in(
        uow_for(producer_actor), task, 'session-A', target_stage='inspect',
    ) is None


def test_same_role_guard_also_applies_after_entry_to_inspection():
    task = task_for(process(), current='inspect', status=TaskStatus.ACTIVE, outcome=None)
    assert require_reviewer_identity_in(uow_for(), task, 'session-A') is None


def test_different_roles_reject_same_effective_session():
    task = task_for(process('author', 'assayer'))
    with pytest.raises(DomainError, match='equals executor'):
        require_reviewer_identity_in(uow_for(), task, 'session-A', target_stage='inspect')


def test_different_roles_accept_distinct_effective_session():
    task = task_for(process('author', 'assayer'))
    receipt = require_reviewer_identity_in(uow_for(), task, 'session-B', target_stage='inspect')
    assert receipt['executor_actor'] == 'session-A'
    assert receipt['reviewer_actor'] == 'session-B'
    assert receipt['distinct_actor'] is True


def test_different_roles_fail_closed_if_producer_identity_unavailable():
    task = task_for(process('author', 'assayer'))
    with pytest.raises(DomainError, match='identity provenance is unavailable'):
        require_reviewer_identity_in(uow_for(None), task, 'session-B', target_stage='inspect')


def test_actual_producing_stage_role_is_used_not_a_relay_or_previous_list_index():
    raw = process('author', 'assayer')
    # Production may return through a remediation loop. Identity.stage is the source.
    repair = deepcopy(raw['stages'][0])
    repair.update(id='repair', role='assayer', handler='revise', rework_targets=['repair'])
    raw['stages'][1]['transitions']['changes_requested'] = 'repair'
    raw['stages'].append(repair)
    task = task_for(raw, current='inspect', status=TaskStatus.ACTIVE, outcome=None)
    assert require_reviewer_identity_in(uow_for('session-A', 'repair'), task, 'session-A') is None


def test_public_stage_patch_changes_role_without_changing_handler_or_source():
    original = process('author', 'assayer')
    updated = GoalTypeDefinition.build('role-contract', original, [{
        'op': 'patch_stage', 'id': 'inspect', 'set': {'role': 'author'},
    }])
    assert original['stages'][1]['role'] == 'assayer'
    assert updated.data['stages'][1]['handler'] == 'inspect'
    assert updated.data['stages'][1]['role'] == 'author'
    assert progression_step(task_for(updated.data), 'inspect', False).kind == 'advance'


@pytest.mark.parametrize('goal,stage,expected', [
    ('verification', 'self_inspection', 'executor'),
    ('review', 'planning', 'reviewer'),
    ('review', 'self_inspection', 'reviewer'),
    ('development', 'implementation_inspection', 'reviewer'),
])
def test_shipped_presets_keep_documented_roles_including_handler_exceptions(goal, stage, expected):
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[2]
    for folder in ('config/catalogue/processes', 'config/catalogue/process-templates',
                   'config/projects/ai-poise/config/processes'):
        value = json.loads((root / folder / (goal + '.json')).read_text())
        definition = GoalTypeDefinition.parse(value.get('process', value))
        assert RouteDefinition.from_process(definition.data).node(stage).role == expected


def test_role_change_roundtrips_through_public_config_owner(tmp_path):
    import json
    from pathlib import Path
    from poise.composition import goal_config_tools
    from tests.goal_config.helpers import settings, request

    editor, selection = settings(tmp_path)
    api = goal_config_tools(editor)
    created = api.apply_batch(request('create', 'writing', None, [], selection=selection))
    target = Path(created['config_path'])
    before = json.loads(target.read_text())
    assert next(stage for stage in before['stages'] if stage['id'] == 'audit')['role'] == 'reviewer'
    changed = api.apply_batch(request('update', 'writing', created['revision'], [
        {'op': 'patch_stage', 'id': 'audit', 'set': {'role': 'executor'}},
    ], 'configure-self-review'))
    persisted = GoalTypeDefinition.parse(json.loads(target.read_text()))
    assert persisted.revision == changed['revision']
    audit = next(stage for stage in persisted.data['stages'] if stage['id'] == 'audit')
    assert (audit['handler'], audit['role']) == ('inspect', 'executor')
    assert next(stage for stage in before['stages'] if stage['id'] == 'audit')['role'] == 'reviewer'
