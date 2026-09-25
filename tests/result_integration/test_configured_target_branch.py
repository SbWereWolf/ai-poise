"""Accepted results follow configured refs, independently of tool installation."""
from pathlib import Path
import hashlib

import pytest

from conftest import git, write_json
from .helpers import (
    integration_guard_method, integration_input, prepare_completed_task, request,
    source_change,
)


@pytest.mark.parametrize('target', ['main', 'master', 'poise-stride-v2'])
def test_task_starts_and_integrates_only_on_configured_branch(project, target):
    app = project['app']
    source_root = Path(__file__).resolve().parents[2] / 'src'
    tool_before = {
        str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_root.rglob('*.py')
    }
    for branch in ['master', 'poise-stride-v2']:
        git(app, 'branch', branch, 'main')
    git(app, 'checkout', target)
    (app / 'target-only.txt').write_text('selected integration base\n')
    git(app, 'add', 'target-only.txt')
    git(app, 'commit', '-m', 'test: configured target base')
    before = {b: git(app, 'rev-parse', b) for b in ['main', 'master', 'poise-stride-v2']}
    project['cfg']['git']['base_ref'] = target
    project['cfg']['git']['branch_template'] = 'tasks/v2/{task_id}'
    write_json(project['config_path'], project['cfg'])

    tools, worktree, accepted = prepare_completed_task(
        project, source_change,
        methods=[integration_guard_method()], checks=['INTEGRATION_GUARD'],
    )
    assert (worktree / 'target-only.txt').read_text() == 'selected integration base\n'
    assert git(worktree, 'symbolic-ref', '--short', 'HEAD') == 'tasks/v2/T1'
    assert git(worktree, 'merge-base', 'HEAD', target) == before[target]
    assert tools.runtime.task_queries.record('T1')['status'] == 'completed'
    payload = integration_input(project, accepted)
    payload['expected_target_commit'] = before[target]
    result = tools.invoke(request('integrate', payload))

    assert result['status'] == 'integrated'
    assert result['accepted_commit'] == accepted
    assert result['target_after'] == git(app, 'rev-parse', target)
    assert result['last_included_target'] == before[target]
    assert git(app, 'symbolic-ref', '--short', 'HEAD') == target
    assert git(app, 'show', f'{target}:src/feature.py') == 'VALUE = 1'
    assert all(check['passed'] for check in result['checks'])
    for branch in before:
        if branch != target:
            assert git(app, 'rev-parse', branch) == before[branch]
    assert not worktree.exists()
    assert git(app, 'status', '--porcelain') == ''
    tool_after = {
        str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_root.rglob('*.py')
    }
    assert tool_after == tool_before
