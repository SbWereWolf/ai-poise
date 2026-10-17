"""Local Task work does not depend on retired material-transport settings."""
import pytest

from batch.helpers import bootstrap, configure, request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError


def tools_without_transport(project, actor):
    project['cfg']['runtime_services'].pop('transfer', None)
    project['cfg']['runtime_services'].pop('handoff', None)
    configure(project)
    return WorkTools(WorkPoise(project['config_path'], actor))


def test_local_bootstrap_without_transport_service(project):
    tools = tools_without_transport(project, 'local')
    context = bootstrap(tools, project)
    assert context['stage'] == project['workflow']['stages'][0]['id']
    assert tools.runtime.current_task()['id'] == 'T1'
    assert tools.runtime.current_task()['claimed_by'] == 'local'


def test_existing_local_task_reopens_after_retired_settings_are_removed(project):
    configure(project)
    original = WorkTools(WorkPoise(project['config_path'], 'owner'))
    context = bootstrap(original, project)
    tools = tools_without_transport(project, 'owner')
    reopened = tools.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert reopened['stage'] == context['stage']
    assert reopened['iteration'] == context['iteration']
    assert reopened['worktree'] == context['worktree']
    assert tools.runtime.current_task()['claimed_by'] == 'owner'


def test_unused_unknown_retired_settings_do_not_affect_local_work(project):
    project['cfg']['runtime_services']['handoff'] = 'unused retired setting'
    project['cfg']['runtime_services']['transfer'] = 'unused retired setting'
    configure(project)
    tools = WorkTools(WorkPoise(project['config_path'], 'local'))
    context = bootstrap(tools, project)
    assert context['status'] == 'active'
    assert tools.runtime.current_task()['claimed_by'] == 'local'


def test_next_participant_uses_the_same_workspace_without_transport_settings(project):
    sender = tools_without_transport(project, 'sender')
    context = bootstrap(sender, project)
    released = sender.invoke(request('release', {
        'task_id': 'T1', 'request_id': 'local-release',
        'expected_version': sender.runtime.current_task()['_version'],
        'reason': 'Continue the same local Task.',
    }))
    assert released['status'] == 'ownership_released'
    receiver = tools_without_transport(project, 'receiver')
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert resumed['stage'] == context['stage']
    assert resumed['iteration'] == context['iteration']
    assert resumed['worktree'] == context['worktree']
    assert receiver.runtime.current_task()['claimed_by'] == 'receiver'


@pytest.mark.parametrize('operation', ['handoff', 'transfer'])
@pytest.mark.parametrize('configured', [False, True])
def test_removed_working_material_operation_is_unsupported_with_any_old_settings(project, operation, configured):
    if configured:
        project['cfg']['runtime_services']['handoff'] = 'unused retired setting'
        project['cfg']['runtime_services']['transfer'] = 'unused retired setting'
        configure(project)
        tools = WorkTools(WorkPoise(project['config_path'], 'local'))
    else:
        tools = tools_without_transport(project, 'local')
    before = tools.runtime.current_task()
    with pytest.raises(PoiseError, match='^Unknown work operation$'):
        tools.invoke(request(operation, {}))
    assert tools.runtime.current_task() == before
