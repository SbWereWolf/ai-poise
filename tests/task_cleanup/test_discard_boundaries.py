"""Observe real cleanup effects at the common adapter, without transport deletion."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from result_integration.helpers import integration_input, prepare_completed_task
from terminal_materials.helpers import arrange
from .helpers import branch_exists, cleanup_input, prepare_cancelled_task


def observe_commands(monkeypatch, adapter):
    calls = []
    original = adapter._run

    def observed(repository, *arguments):
        calls.append(arguments)
        return original(repository, *arguments)

    monkeypatch.setattr(adapter, '_run', observed)
    return calls


def removals(calls):
    return [call for call in calls if call[:2] == ('worktree', 'remove')]


def test_authorized_cancel_discards_dirty_owned_work_and_preserves_foreign_work(project, monkeypatch):
    tools, root, _, _ = arrange(project)
    record = tools.runtime.current_task()
    tree = Path(record['worktree'])
    (tree / 'unfinished.txt').write_bytes(b'Unrequested unfinished Task material\n')
    foreign = project['root'].parent / 'foreign-worktree'
    git(project['app'], 'worktree', 'add', '-b', 'foreign-owned', str(foreign), 'main')
    keep = foreign / 'operator-note.txt'
    keep.write_bytes(b'Foreign unfinished material\n')
    foreign_head = git(foreign, 'rev-parse', 'HEAD')
    primary_head = git(project['app'], 'rev-parse', 'HEAD')
    calls = observe_commands(monkeypatch, tools.runtime.cleanup_tools.adapter)

    result = tools.invoke(request('cancel', {'reason': 'Discard this Task and its unfinished working materials.'}))

    assert result['status'] == 'cancelled'
    assert result['cleanup']['disposition']['kind'] == 'discard_authorized'
    assert removals(calls) == [('worktree', 'remove', '--force', str(tree))]
    assert not tree.exists() and not root.exists()
    assert not branch_exists(project)
    assert keep.read_bytes() == b'Foreign unfinished material\n'
    assert git(foreign, 'rev-parse', 'HEAD') == foreign_head
    assert git(project['app'], 'rev-parse', 'HEAD') == primary_head


def test_completed_integration_removes_worktree_without_force(project, monkeypatch):
    tools, tree, commit = prepare_completed_task(
        project, lambda path: (path / 'src/feature.py').write_bytes(b'VALUE = 1\n'))
    calls = observe_commands(monkeypatch, tools.runtime.cleanup_tools.adapter)

    result = tools.invoke(request('integrate', integration_input(project, commit)))

    assert result['status'] == 'integrated', 'COMPLETED_INTEGRATION_MUST_FINISH_UNFORCED_CLEANUP'
    assert removals(calls) == [('worktree', 'remove', str(tree))]
    assert not tree.exists() and not branch_exists(project)
    assert git(project['app'], 'show', 'main:src/feature.py') == 'VALUE = 1'


@pytest.mark.parametrize('fault', ['unknown-intent', 'foreign-resource', 'changed-resource'])
def test_unresolved_or_changed_cleanup_never_deletes_work(project, monkeypatch, fault):
    tools, tree, commit, _ = prepare_cancelled_task(project)
    adapter = tools.runtime.cleanup_tools.adapter
    foreign = project['root'].parent / 'foreign-worktree'
    git(project['app'], 'worktree', 'add', '-b', 'foreign-owned', str(foreign), 'main')
    keep = foreign / 'operator-note.txt'
    keep.write_bytes(b'Foreign unfinished material\n')
    if fault == 'changed-resource':
        (tree / 'src/changed.py').write_bytes(b'CHANGED = True\n')
        git(tree, 'add', 'src/changed.py')
        git(tree, 'commit', '-m', 'Preserve a changed terminal resource')
    if fault in ('unknown-intent', 'foreign-resource'):
        with tools.runtime.store.transaction() as db:
            data, version = db.execute(
                'SELECT data,version FROM task_execution WHERE task_id=?', ('T1',)).fetchone()
            execution = json.loads(data)
            pending = deepcopy(execution['pending'])
            if fault == 'unknown-intent':
                pending = {'kind': 'unresolved-fixture-operation', 'request_id': 'unknown'}
            else:
                owned = next(item for item in pending['resources'] if item['kind'] == 'worktree')
                owned['path'] = str(foreign)
            execution['pending'] = pending
            changed = db.execute(
                'UPDATE task_execution SET data=?,version=version+1 WHERE task_id=? AND version=?',
                (json.dumps(execution), 'T1', version))
            assert changed.rowcount == 1
    primary_head = git(project['app'], 'rev-parse', 'HEAD')
    foreign_head = git(foreign, 'rev-parse', 'HEAD')
    before_head = git(tree, 'rev-parse', 'HEAD')
    before_work = git(tree, 'status', '--porcelain', '--untracked-files=all')
    before_source = (tree / 'src/cancelled.py').read_bytes()
    calls = observe_commands(monkeypatch, adapter)

    if fault == 'unknown-intent':
        with pytest.raises(PoiseError):
            tools.invoke(request('cleanup', cleanup_input(commit)))
        with tools.runtime.store.transaction() as db:
            after = json.loads(db.execute(
                'SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        assert after['pending'] == {'kind': 'unresolved-fixture-operation', 'request_id': 'unknown'}
    else:
        result = tools.invoke(request('cleanup', cleanup_input(commit)))
        assert result['status'] == 'cleanup_blocked'
        assert result['blocker']['reason'] == 'owned_resource_changed'
    assert removals(calls) == []
    assert not any(call[:2] == ('update-ref', '-d') for call in calls)
    assert tree.is_dir() and branch_exists(project)
    assert git(tree, 'rev-parse', 'HEAD') == before_head
    assert git(tree, 'status', '--porcelain', '--untracked-files=all') == before_work
    assert (tree / 'src/cancelled.py').read_bytes() == before_source
    assert keep.read_bytes() == b'Foreign unfinished material\n'
    assert git(project['app'], 'rev-parse', 'HEAD') == primary_head
    assert git(foreign, 'rev-parse', 'HEAD') == foreign_head
