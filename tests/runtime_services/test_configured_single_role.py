"""Real persisted task execution supports one role without a fabricated reviewer."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result, verify
from conftest import WorkPoise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from .test_stage_progression import configure_progression, advance, bootstrap, handoff


def _through_implementation(project, *, role='executor', review_role='executor'):
    stages = configure_progression(project)
    for stage in project['process']['stages']:
        stage['role'] = review_role if stage['id'] == 'code_review' else role
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    tools = WorkTools(WorkPoise(project['config_path'], 'session-A'))
    first = bootstrap(tools, project['task'])
    assert first['workflow']['role'] == role
    add_test(first['worktree'])
    assert verify(tools, result(first))['status'] == 'verified'
    implementation = advance(tools)
    assert implementation['status'] == 'progression_work_required'
    Path(implementation['worktree'], 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    assert verify(tools, result(implementation))['status'] == 'verified'
    return tools


@pytest.mark.parametrize('role', ['executor', 'maintainer'])
def test_one_session_completes_red_green_inspection_and_acceptance(project, role):
    tools = _through_implementation(project, role=role, review_role=role)
    review = advance(tools)
    assert review['status'] == 'progression_target_reached'
    assert review['stage'] == 'code_review'
    assert review['workflow']['role'] == role
    payload = result(review)
    payload['stage_work']['coverage'] = 'Checked the implementation against the required result.'
    assert verify(tools, payload)['status'] == 'verified'
    assert tools.invoke(request('accept', {}))['status'] == 'completed'
    saved = tools.runtime.task_queries.record('T1')
    assert saved['status'] == 'completed'
    history = tools.runtime.task_queries.history('T1')
    assert not any(event['event'] in {'handed_off', 'handoff_resumed'} for event in history)
    with tools.runtime.store.unit_of_work() as unit:
        identity = unit.tasks.review_identity('T1')
        assert identity['executor_actor'] == 'session-A'
    assert {item['actor'] for item in history if item.get('actor')} == {'session-A'}


def test_single_role_can_reacquire_its_review_after_handoff(project):
    tools = _through_implementation(project)
    assert handoff(tools)['status'] == 'handed_off'
    resumed = WorkTools(WorkPoise(project['config_path'], 'session-A'))
    bootstrap(resumed, {'id': 'T1'})
    review = advance(resumed)
    assert review['stage'] == 'code_review'
    payload = result(review)
    payload['stage_work']['coverage'] = 'Self-review after a real resume.'
    assert verify(resumed, payload)['status'] == 'verified'


def test_different_roles_keep_same_actor_rejection_at_public_acquisition(project):
    tools = _through_implementation(project, role='author', review_role='assayer')
    boundary = advance(tools)
    assert boundary['status'] == 'role_handoff_required'
    assert (boundary['from_role'], boundary['to_role']) == ('author', 'assayer')
    assert handoff(tools)['status'] == 'handed_off'
    with pytest.raises(PoiseError, match='equals executor'):
        bootstrap(tools, {'id': 'T1'})
    other = WorkTools(WorkPoise(project['config_path'], 'session-B'))
    bootstrap(other, {'id': 'T1'})
    review = advance(other)
    assert review['stage'] == 'code_review'
    payload = result(review)
    payload['stage_work']['coverage'] = 'Distinct participant performed the configured inspection.'
    assert verify(other, payload)['status'] == 'verified'


def test_task_role_snapshot_survives_external_process_edits(project):
    tools = _through_implementation(project)
    project['process']['stages'][-1]['role'] = 'reviewer'
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    reloaded = WorkTools(WorkPoise(project['config_path'], 'session-A'))
    review = advance(reloaded)
    assert review['stage'] == 'code_review'
    assert review['workflow']['role'] == 'executor'


def test_same_session_inspects_red_tests_before_implementation_and_green_code(project):
    """The same inspect handler checks RED and GREEN; its role remains process data."""
    process = deepcopy(project['process'])
    for position, stage in enumerate(process['stages']):
        stage['role'] = 'executor'
        if stage['id'] in ('test_review', 'code_review'):
            following = 'implementation' if stage['id'] == 'test_review' else None
            producer = 'tests' if stage['id'] == 'test_review' else 'implementation'
            stage.update(handler='inspect', transitions={
                'clear': following, 'changes_requested': producer,
            })
    write_json(project['root'] / 'config/processes/development.json', process)
    task = deepcopy(project['task'])
    task['checks'] = {
        'tests': ['RED'], 'test_review': ['RED'],
        'implementation': ['GREEN'], 'code_review': ['GREEN'],
    }
    for method in task['methods']:
        if method['id'] == 'RED':
            method['verification_plan']['red_stages'] = ['tests', 'test_review']
    tools = WorkTools(WorkPoise(project['config_path'], 'sole-session'))
    context = bootstrap(tools, task)
    reports = []
    for index, stage_id in enumerate(('tests', 'test_review', 'implementation', 'code_review')):
        if index:
            context = tools.invoke(request('bootstrap', {
                'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None,
            }))
        assert context['stage'] == stage_id
        assert context['workflow']['role'] == 'executor'
        if stage_id == 'tests':
            add_test(context['worktree'])
        elif stage_id == 'implementation':
            Path(context['worktree'], 'src/double.py').write_text('def double(n):\n    return n * 2\n')
        payload = result(context)
        if stage_id in ('test_review', 'code_review'):
            payload['stage_work'] = {
                'coverage': 'Inspect the test oracle and observed RED/GREEN result.',
                'findings': [], 'resolution_decisions': [],
            }
        report = verify(tools, payload)
        assert report['status'] == 'verified'
        assert report['stage'] == stage_id
        reports.append(report)
    assert [item['handler'] for item in reports] == ['produce', 'inspect', 'produce', 'inspect']
    assert tools.invoke(request('accept', {}))['status'] == 'completed'
    history = tools.runtime.task_queries.history('T1')
    verified = [item for item in history if item['event'] == 'verified']
    assert [item['stage'] for item in verified] == ['tests', 'test_review', 'implementation', 'code_review']
    assert {item['actor'] for item in verified} == {'sole-session'}
    assert not any(item['event'] == 'handed_off' for item in history)
