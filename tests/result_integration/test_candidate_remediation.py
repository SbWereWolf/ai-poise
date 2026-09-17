"""Task0138: retry targets the exact repaired child commit, never accepted history."""
from pathlib import Path
import subprocess

import pytest

from conftest import git
from poise.common import PoiseError
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import (prepare_completed_task, source_change, integration_guard_method,
                      integration_input, request, advance_ref_with_same_tree, optional_ref)
from .test_existing_task_worktree_completion import _fingerprint


@pytest.fixture
def failed(project):
    method = integration_guard_method()
    method['argv'][-1] += ";assert not pathlib.Path('target-marker').exists()"
    tools, worktree, accepted = prepare_completed_task(project, source_change,
        methods=[method], checks=[method['id']])
    (project['app'] / 'target-marker').write_text('incompatible target\n')
    git(project['app'], 'add', 'target-marker')
    git(project['app'], 'commit', '-m', 'test: incompatible target')
    packet = request('integrate', integration_input(project, accepted))
    first = tools.invoke(packet)
    assert first['phase'] == 'checks_failed'
    return tools, worktree, accepted, packet, first


def repair(worktree):
    git(worktree, 'rm', 'target-marker')
    git(worktree, 'commit', '-m', 'fix: adapt accepted result to current target')
    return git(worktree, 'rev-parse', 'HEAD')


def pending(tools):
    return tools.runtime.task_queries.record('T1')['pending']


@pytest.mark.parametrize('drift', [False, True])
def test_repaired_candidate_rechecked_published_replayed_and_cleaned(failed, project, monkeypatch, drift):
    tools, worktree, accepted, packet, first = failed
    candidate = repair(worktree)
    original = RuntimeResultIntegration._run
    drifted = []
    def run(self, cwd, *args, env=None):
        if drift and args[:2] == ('merge', '--ff-only') and not drifted:
            parent = git(project['app'], 'rev-parse', 'HEAD')
            drifted.append(advance_ref_with_same_tree(project['app'], parent, 'test: another target'))
        return original(self, cwd, *args, env=env)
    monkeypatch.setattr(RuntimeResultIntegration, '_run', run)
    done = tools.invoke(packet)
    assert done['status'] == 'integrated'
    assert done['accepted_commit'] == accepted
    assert done['checks'][0] == first['checks'][0]
    assert len(done['checks']) == (3 if drift else 2)
    for check in done['checks'][1:]:
        assert check['passed']
        assert 'head='+check['integration_head'] in Path(check['stdout']).read_text()
    assert done['checks'][1]['integration_head'] == candidate
    assert done['checks'][-1]['integration_head'] == done['target_after']
    assert git(project['app'], 'merge-base', '--is-ancestor', candidate, 'HEAD') == ''
    assert done['integration_head'] == git(project['app'], 'rev-parse', 'HEAD')
    assert not worktree.exists()
    assert optional_ref(project['app'], done['task_branch']) is None
    assert done['cleanup'] == {'task_worktree':'removed','task_branch':'deleted','temporary_backups':'removed'}
    assert tools.invoke(packet)['replayed'] is True


@pytest.mark.parametrize('kind', ['unstaged', 'staged', 'untracked', 'branch', 'accepted_lost', 'target_lost', 'merge'])
def test_invalid_retry_rejected_before_integration_state_change(failed, project, kind):
    tools, worktree, accepted, packet, first = failed
    repair(worktree)
    if kind in ('unstaged', 'staged', 'untracked'):
        path = 'scratch.txt' if kind == 'untracked' else 'src/feature.py'
        (worktree/path).write_text('foreign WIP\n')
        if kind == 'staged': git(worktree, 'add', path)
    elif kind == 'branch':
        git(worktree, 'switch', '-c', 'foreign-branch')
    elif kind in ('accepted_lost', 'target_lost'):
        git(worktree, 'reset', '--hard', first['last_included_target'] if kind=='accepted_lost' else accepted)
    else:
        merge = Path(git(worktree, 'rev-parse', '--git-path', 'MERGE_HEAD'))
        merge.write_text(first['last_included_target']+'\n')
    before = pending(tools)
    own = _fingerprint(worktree); target = _fingerprint(project['app'])
    try:
        with pytest.raises(PoiseError): tools.invoke(packet)
    finally:
        assert pending(tools) == before
        assert _fingerprint(worktree) == own
        assert _fingerprint(project['app']) == target


def test_dirtying_candidate_after_checks_cannot_publish(failed, project, monkeypatch):
    tools, worktree, accepted, packet, first = failed
    repair(worktree)
    target = git(project['app'], 'rev-parse', 'HEAD')
    original = RuntimeResultIntegration._run_checks
    def dirty_after(self, record, run):
        checked = original(self, record, run)
        (worktree/'foreign-after-checks.txt').write_text('preserve me\n')
        return checked
    monkeypatch.setattr(RuntimeResultIntegration, '_run_checks', dirty_after)
    done = tools.invoke(packet)
    assert done['status'] == 'blocked'
    assert done['phase'] == 'publication_failed'
    assert git(project['app'], 'rev-parse', 'HEAD') == target
    assert (worktree/'foreign-after-checks.txt').read_text() == 'preserve me\n'


def test_crash_after_recording_repaired_head_resumes_without_manual_state_edit(failed, project, monkeypatch):
    tools, worktree, accepted, packet, first = failed
    candidate = repair(worktree)
    original = RuntimeResultIntegration._save
    interrupted = []
    def save(self, task_id, run, version):
        original(self, task_id, run, version)
        if (not interrupted and run.history[-1].get('event') == 'checks_retry_started'):
            interrupted.append(True)
            raise PoiseError('injected failure after durable retry')
    monkeypatch.setattr(RuntimeResultIntegration, '_save', save)
    with pytest.raises(PoiseError, match='durable retry'):
        tools.invoke(packet)
    assert pending(tools)['integration_head'] == candidate
    assert pending(tools)['phase'] == 'candidate_ready'
    assert git(project['app'], 'rev-parse', 'HEAD') == first['last_included_target']
    monkeypatch.setattr(RuntimeResultIntegration, '_save', original)
    done = tools.invoke(packet)
    assert done['status'] == 'integrated'
    assert done['accepted_commit'] == accepted
    assert done['checks'][-1]['integration_head'] == candidate
