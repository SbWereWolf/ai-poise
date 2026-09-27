"""HARNESS-20260927-01: Task existence is not a configuration effect."""
from copy import deepcopy
from pathlib import Path
import re

import pytest

from poise.application.work import WorkTools
from poise.common import configured_root, descendant, load_config
from poise.modules.foundation.errors import PoiseError, VersionConflict
from poise.runtime import Poise
from tests.conftest import DeterministicClock
from batch.helpers import request
from sprints.helpers import draft as draft_sprint, publish as publish_sprint
from projects.test_update import installed_project, update_request, update_tools, work_tools


def existing_task(project, config_path, status):
    tools = work_tools(config_path, 'manifest-owner')
    task = deepcopy(project['task'])
    if status == 'available':
        task.update(id='WAITING', sprint_id='S')
        draft = draft_sprint(tools, [task])
        publish_sprint(tools, draft['revision'])
    else:
        tools.invoke(request('bootstrap', {
            'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
    saved = tools.runtime.task_queries.record(task['id'])
    assert saved['status'] == status
    root, config, _ = load_config(config_path)
    database = descendant(configured_root(root, config['paths']['state']), config['paths']['database'])
    return tools, task['id'], saved, database


def value_at(document, path):
    for key in path:
        document = document[key]
    return document


@pytest.mark.parametrize('status', ['available', 'active'])
@pytest.mark.parametrize('path,value', [
    (['git', 'commit_pattern'], '(?s).+'),
    (['git', 'author_name'], 'Explicitly configured author'),
    (['limits', 'output_chars'], 9000),
    (['runtime_services', 'transfer', 'max_total_bytes'], 268435456),
])
def test_ordinary_manifest_update_preserves_unfinished_task(project, status, path, value):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, status)
    state_bytes = database.read_bytes()
    wip = None
    if before['worktree']:
        wip = Path(before['worktree']) / 'preserved-wip.txt'
        wip.write_text('Owned work must remain exactly as it was.\n')
    packet = update_request(config, created['revision'], manifest_edits=[{'path': path, 'value': value}])

    published = update_tools(settings).apply(packet)

    assert published['status'] == 'updated'
    assert published['changed'] is True
    assert value_at(load_config(config)[1], path) == value
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before
    if wip is not None:
        assert wip.read_text() == 'Owned work must remain exactly as it was.\n'
    assert update_tools(settings).apply(packet)['replayed'] is True
    assert database.read_bytes() == state_bytes
    if status == 'active':
        fresh = WorkTools(Poise(config, 'manifest-owner', DeterministicClock()))
        resumed = fresh.invoke(request('bootstrap', {
            'task': None, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
        assert resumed['task'] == tid
        assert resumed['stage'] == before['process']['stages'][before['stage_index']]['id']
        assert fresh.runtime.task_queries.record(tid)['process'] == before['process']
        if path == ['git', 'commit_pattern']:
            assert re.fullmatch(fresh.runtime.cfg['git']['commit_pattern'],
                                'Keep policy repair available\n\nPreserve Task state.')


def test_parent_git_edit_is_classified_by_actual_values(project):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, 'active')
    state_bytes = database.read_bytes()
    git_settings = deepcopy(load_config(config)[1]['git'])
    git_settings['commit_pattern'] = '(?s).+'
    packet = update_request(config, created['revision'],
                            manifest_edits=[{'path': ['git'], 'value': git_settings}])

    assert update_tools(settings).apply(packet)['status'] == 'updated'
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before


@pytest.mark.parametrize('path', [
    ['git', 'repository'], ['git', 'remote'],
    ['batch', 'artifact_directories'], ['runtime_services', 'handoff', 'directory'],
])
def test_no_op_resource_edit_does_not_require_quiescence(project, path):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, 'active')
    state_bytes = database.read_bytes()
    value = deepcopy(value_at(load_config(config)[1], path))
    packet = update_request(config, created['revision'], manifest_edits=[{'path': path, 'value': value}])

    result = update_tools(settings).apply(packet)

    assert result['changed'] is False
    assert result['revision'] == created['revision']
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before


def test_available_task_without_workspace_does_not_freeze_git_binding(project):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, 'available')
    assert before['worktree'] is None
    state_bytes = database.read_bytes()
    packet = update_request(config, created['revision'],
                            manifest_edits=[{'path': ['git', 'remote'], 'value': 'new-backup'}])

    assert update_tools(settings).apply(packet)['changed'] is True
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before


@pytest.mark.parametrize('status', ['available', 'active'])
@pytest.mark.parametrize('path,value', [
    (['batch', 'artifact_directories', 'task'], 'other-artifacts'),
    (['batch', 'artifact_lock'], 'other-artifact.lock'),
    (['runtime_services', 'handoff', 'directory'], 'other-handoffs'),
    (['runtime_services', 'transfer', 'directory'], 'other-transfers'),
    (['accounting', 'storage', 'database'], 'other-events.sqlite'),
])
def test_storage_binding_change_retains_integrity_guard(project, status, path, value):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, status)
    manifest_bytes, state_bytes = config.read_bytes(), database.read_bytes()
    packet = update_request(config, created['revision'], manifest_edits=[
        {'path': ['git', 'commit_pattern'], 'value': '(?s).+'},
        {'path': path, 'value': value},
    ])

    with pytest.raises(PoiseError, match='quiescent|active'):
        update_tools(settings).apply(packet)

    assert config.read_bytes() == manifest_bytes
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before
    receipt = project['root'] / packet['receipt_path']
    assert not receipt.exists()
    assert not receipt.with_name(receipt.name + '.pending').exists()


def test_live_policy_update_keeps_stale_revision_rejection(project):
    settings, config, created = installed_project(project)
    tools, tid, before, database = existing_task(project, config, 'active')
    packet = update_request(config, created['revision'],
                            manifest_edits=[{'path': ['git', 'commit_pattern'], 'value': '(?s).+'}])
    update_tools(settings).apply(packet)
    manifest_bytes, state_bytes = config.read_bytes(), database.read_bytes()
    stale = deepcopy(packet)
    stale.update(request_id='stale-policy', receipt_path='operations/stale-policy.json')
    stale['manifest_edits'][0]['value'] = '.+'

    with pytest.raises(VersionConflict, match='expected known revision'):
        update_tools(settings).apply(stale)

    assert config.read_bytes() == manifest_bytes
    assert database.read_bytes() == state_bytes
    assert tools.runtime.task_queries.record(tid) == before
