"""Worktree-free execution stores a tree without weakening exact-commit proof."""
from copy import deepcopy

import pytest

from batch.helpers import request
from conftest import WorkPoise, git, write_json
from ownership.test_atomic_ownership import _configure_process, _fingerprint, _task
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from tasks.test_planning_flexibility import POLICY


def _prepare(project, *, worktree_required=False):
    _configure_process(project, worktree_required)
    project['cfg']['automatic_checks'] = []
    write_json(project['config_path'], project['cfg'])
    task = _task(project, 'TREE-PROOF')
    task['methods'] = []
    task['method_inputs'] = []
    task['checks'] = {stage['id']: [] for stage in project['process']['stages']}
    task['evidence_plan'] = {
        stage['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []}
        for stage in project['process']['stages']
    }
    return WorkTools(WorkPoise(project['config_path'], 'tree-proof-owner')), task


def _start(tools, task):
    return tools.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))


def _result(context):
    result = deepcopy(context['result_template'])
    result['sections']['report'] = 'The unchanged repository is checked, not committed.'
    return result


@pytest.mark.parametrize('worktree_required', [False, True], ids=['task_only', 'worktree'])
def test_execution_separates_reserved_commit_and_tree(project, worktree_required):
    tools, task = _prepare(project, worktree_required=worktree_required)
    commit = git(project['app'], 'rev-parse', 'HEAD')
    tree = git(project['app'], 'rev-parse', 'HEAD^{tree}')
    context = _start(tools, task)
    execution = tools.runtime.task_queries.record(task['id'])
    assert commit != tree
    assert execution['base'] == commit
    assert execution['entry_tree'] == tree
    assert git(project['app'], 'cat-file', '-t', execution['base']) == 'commit'
    assert git(project['app'], 'cat-file', '-t', execution['entry_tree']) == 'tree'
    assert (context['worktree'] is not None) is worktree_required
    verified = tools.invoke(request('verify', {'result': _result(context), 'artifacts': []}))
    assert verified['status'] == 'verified'
    assert verified['commit'] == commit
    assert tools.runtime.task_queries.record(task['id'])['last_report']['verified_tree'] == tree


def test_ready_standalone_uses_the_same_tree_reservation(project):
    tools, task = _prepare(project)
    patch = {key: value for key, value in task.items() if key not in {'id', 'goal_type', 'sprint_id'}}
    created = tools.invoke(request('task', {
        'action': 'create', 'request_id': 'tree-proof-create', 'task_id': task['id'],
        'sprint_id': None,
    }))
    patch.update(goal_type=task['goal_type'], process=deepcopy(project['process']),
                 planning={'schema': 'task-planning-1', 'template': None,
                           'restart_revision_policy': deepcopy(POLICY)})
    edited = tools.invoke(request('task', {
        'action': 'edit', 'request_id': 'tree-proof-edit', 'task_id': task['id'],
        'expected_revision': created['revision'], 'patch': patch, 'remove': [],
    }))
    ready = tools.invoke(request('task', {
        'action': 'ready', 'request_id': 'tree-proof-ready', 'task_id': task['id'],
        'expected_revision': edited['revision'],
    }))
    assert ready['status'] == 'available'
    context = _start(tools, {'id': task['id']})
    assert context['worktree'] is None
    assert tools.runtime.task_queries.record(task['id'])['entry_tree'] == git(
        project['app'], 'rev-parse', 'HEAD^{tree}')
    verified = tools.invoke(request('verify', {'result': _result(context), 'artifacts': []}))
    assert verified['status'] == 'verified'
    assert verified['commit'] == git(project['app'], 'rev-parse', 'HEAD')


@pytest.mark.parametrize('change', ['unstaged', 'staged', 'untracked', 'mode'])
@pytest.mark.parametrize('timing', ['before_bootstrap', 'after_bootstrap'])
def test_repository_changes_are_rejected_without_mutating_git_or_wip(project, change, timing):
    tools, task = _prepare(project)

    def mutate():
        source = project['app'] / 'src/double.py'
        if change == 'mode':
            source.chmod(source.stat().st_mode ^ 0o111)
        elif change == 'untracked':
            (project['app'] / 'local-note.txt').write_text('Preserve this untracked input.\n')
        else:
            source.write_text('def double(n):\n    return n * 2\n')
            if change == 'staged':
                git(project['app'], 'add', 'src/double.py')

    if timing == 'before_bootstrap':
        mutate()
    context = _start(tools, task)
    if timing == 'after_bootstrap':
        mutate()
    before = _fingerprint(project['app'])
    with pytest.raises(PoiseError, match='Commit or code changed after verification'):
        tools.invoke(request('verify', {'result': _result(context), 'artifacts': []}))
    assert _fingerprint(project['app']) == before
    record = tools.runtime.task_queries.record(task['id'])
    assert record['status'] == 'active'
    assert record['last_report'] is None


def test_changed_head_tree_is_not_committed_or_accepted(project):
    tools, task = _prepare(project)
    context = _start(tools, task)
    reserved = tools.runtime.task_queries.record(task['id'])['base']
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    git(project['app'], 'add', 'src/double.py')
    git(project['app'], 'commit', '-m', 'Separate repository change')
    before = _fingerprint(project['app'])
    with pytest.raises(PoiseError, match='Worktree-free or unchanged Task cannot commit'):
        tools.invoke(request('verify', {'result': _result(context), 'artifacts': []}))
    assert _fingerprint(project['app']) == before
    record = tools.runtime.task_queries.record(task['id'])
    assert record['base'] == reserved
    assert record['last_report'] is None


def test_reservation_peels_the_explicit_base_not_the_new_head(project):
    tools, _ = _prepare(project)
    base = git(project['app'], 'rev-parse', 'HEAD')
    tree = git(project['app'], 'rev-parse', base + '^{tree}')
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    git(project['app'], 'add', 'src/double.py')
    git(project['app'], 'commit', '-m', 'Move repository head')
    before = _fingerprint(project['app'])
    record = tools.runtime._execution_reservation('NOT-PERSISTED', base, False)
    assert record['base'] == base
    assert record['entry_tree'] == tree != git(project['app'], 'rev-parse', 'HEAD^{tree}')
    assert _fingerprint(project['app']) == before
    assert tools.runtime.task_queries.record('NOT-PERSISTED') is None


def test_missing_base_rejects_reservation_without_persistence(project):
    tools, _ = _prepare(project)
    before = _fingerprint(project['app'])
    with pytest.raises(PoiseError):
        tools.runtime._execution_reservation('NOT-PERSISTED', '0' * 40, False)
    assert _fingerprint(project['app']) == before
    assert tools.runtime.task_queries.record('NOT-PERSISTED') is None
