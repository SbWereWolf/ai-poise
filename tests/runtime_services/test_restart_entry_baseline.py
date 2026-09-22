"""A restarted entry observes retained HEAD, not abandoned stage changes."""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from conftest import WorkPoise, write_json
from batch.helpers import configure, request, result, verify
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from runtime_services.test_task_restart import (
    execution, git, immutable_audit_rows, process_contract, restart,
)


def prepare(project, *, dedicated=True):
    configure(project)
    process = deepcopy(project['process'])
    stage = deepcopy(process['stages'][0])
    stage.update(id='baseline', read_only=True, allowed_paths=[],
                 transitions={'complete': None}, rework_targets=['baseline'])
    process.update(worktree_required=dedicated, stages=[stage], route={'entry': 'baseline'},
                   content_contract={'sections': [], 'routes': [], 'requirements': []})
    project['cfg']['automatic_checks'] = []
    write_json(project['config_path'], project['cfg'])
    write_json(project['root'] / 'config/processes/development.json', process)
    task = deepcopy(project['task'])
    task.update(process_contract('development', process))
    owner = WorkTools(WorkPoise(project['config_path'], 'restart-owner'))
    ctx = owner.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    root = Path(ctx['worktree']) if dedicated else project['app']
    return owner, ctx, root


def commit_retained(root, text='def double(n):\n    return n * 2\n'):
    (root / 'src/double.py').write_text(text)
    git(root, 'add', 'src/double.py')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-m', 'Preserve authorized repair')
    return git(root, 'rev-parse', 'HEAD')


def restart_ready(owner):
    before = owner.runtime.task_queries.record('T1')
    nb = restart(owner, 'T1', before['version'])
    ready = owner.invoke(request('task', {
        'action': 'ready', 'request_id': 'ready-entry-baseline', 'task_id': 'T1',
        'expected_revision': nb['revision'],
    }))
    assert ready['status'] == 'available'


def start(owner):
    return owner.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))


def bytes_state(root):
    return {'head': git(root, 'rev-parse', 'HEAD'),
            'index': Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index')).read_bytes(),
            'files': {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
                      for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}}


@pytest.mark.parametrize('dedicated', [True, False])
def test_committed_repair_restarts_at_retained_head_without_rewriting_history(project, dedicated):
    owner, _, root = prepare(project, dedicated=dedicated)
    old_execution = execution(owner.runtime, 'T1')
    head = commit_retained(root)
    history = immutable_audit_rows(owner.runtime, 'T1')
    restart_ready(owner)
    before = bytes_state(root)
    ctx = start(owner)
    now = execution(owner.runtime, 'T1')
    assert now['entry_tree'] == git(root, 'rev-parse', head + '^{tree}')
    assert now['base'] == old_execution['base']
    assert now['worktree'] == old_execution['worktree']
    assert now['branch'] == old_execution['branch']
    assert bytes_state(root) == before
    verified = verify(owner, result(ctx, 'Unchanged restarted baseline'))
    assert verified['status'] == 'verified'
    assert verified['commit'] == head
    after = immutable_audit_rows(owner.runtime, 'T1')
    for table, rows in history.items():
        assert all(row in after[table] for row in rows)


@pytest.mark.parametrize('dedicated', [True, False])
@pytest.mark.parametrize('kind', ['staged', 'unstaged', 'untracked', 'mode'])
def test_restart_preserves_dirty_wip_but_never_verifies_it_as_read_only(project, kind, dedicated):
    owner, _, root = prepare(project, dedicated=dedicated)
    commit_retained(root)
    path = root / ('new.txt' if kind == 'untracked' else 'src/double.py')
    if kind == 'mode':
        path.chmod(0o755)
    else:
        path.write_text('not a read-only change\n')
        if kind == 'staged':
            git(root, 'add', 'src/double.py')
    before = bytes_state(root)
    restart_ready(owner)
    ctx = start(owner)
    assert bytes_state(root) == before
    with pytest.raises(PoiseError):
        verify(owner, result(ctx, 'Cannot accept dirty baseline'))
    assert bytes_state(root) == before
    assert owner.runtime.task_queries.record('T1')['status'] == 'active'
    assert execution(owner.runtime, 'T1')['last_report'] is None


def test_entry_observed_at_new_start_not_at_restart_or_ready(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    head = commit_retained(root, 'def double(n):\n    return 2 * n\n')
    ctx = start(owner)
    assert execution(owner.runtime, 'T1')['entry_tree'] == git(root, 'rev-parse', head + '^{tree}')
    assert verify(owner, result(ctx))['status'] == 'verified'


def test_repeated_active_bootstrap_cannot_hide_later_committed_changes(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    start(owner)
    tree = execution(owner.runtime, 'T1')['entry_tree']
    commit_retained(root, 'def double(n):\n    return 9 * n\n')
    before = bytes_state(root)
    ctx = start(owner)
    assert execution(owner.runtime, 'T1')['entry_tree'] == tree
    with pytest.raises(PoiseError):
        verify(owner, result(ctx))
    assert bytes_state(root) == before


@pytest.mark.parametrize('invalid', ['missing', 'other_branch'])
def test_invalid_retained_workspace_rejects_before_start(project, invalid):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    if invalid == 'missing':
        root.rename(root.with_name(root.name + '-preserved'))
    else:
        git(root, 'switch', '-c', 'other-fixture-branch')
    before = owner.runtime.task_queries.record('T1')
    old_execution = execution(owner.runtime, 'T1')
    with pytest.raises(PoiseError):
        start(owner)
    assert owner.runtime.task_queries.record('T1') == before
    assert execution(owner.runtime, 'T1') == old_execution


def test_entry_update_failure_rolls_back_task_start(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    before = owner.runtime.task_queries.record('T1')
    old_execution = execution(owner.runtime, 'T1')
    with owner.runtime.store.transaction() as db:
        db.execute("CREATE TRIGGER deny_entry BEFORE UPDATE ON task_execution "
                   "WHEN NEW.task_id='T1' BEGIN SELECT RAISE(ABORT,'entry-rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='entry-rollback'):
        start(owner)
    assert owner.runtime.task_queries.record('T1') == before
    assert execution(owner.runtime, 'T1') == old_execution


@pytest.mark.parametrize('invalid', ['symlink', 'parent_symlink'])
def test_restart_rejects_symbolic_workspace_identity(project, invalid):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    before = owner.runtime.task_queries.record('T1')
    path = root if invalid == 'symlink' else root.parent
    held = path.with_name(path.name + '-real')
    path.rename(held)
    path.symlink_to(held, target_is_directory=True)
    try:
        with pytest.raises(PoiseError):
            start(owner)
        assert owner.runtime.task_queries.record('T1') == before
    finally:
        path.unlink()
        held.rename(path)


def test_restart_start_does_not_take_foreign_worktree_binding(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    with owner.runtime.store.unit_of_work() as uow:
        uow.ownership.bind_worktree('foreign', 'T1')
    before = owner.runtime.task_queries.record('T1')
    binding = owner.runtime.ownership.snapshot('foreign')
    with pytest.raises(PoiseError, match='foreign|ownership|live status'):
        start(owner)
    assert owner.runtime.task_queries.record('T1') == before
    assert owner.runtime.ownership.snapshot('foreign') == binding


def test_restart_start_rejects_pending_execution_without_effect(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    with owner.runtime.store.unit_of_work() as uow:
        uow.execution.patch('T1', {'pending': 'checks'})
    before = owner.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='pending|unknown'):
        start(owner)
    assert owner.runtime.task_queries.record('T1') == before


def test_failed_task_write_rolls_back_new_entry_tree(project):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    before = owner.runtime.task_queries.record('T1')
    old_execution = execution(owner.runtime, 'T1')
    with owner.runtime.store.transaction() as db:
        db.execute("CREATE TRIGGER deny_start BEFORE UPDATE ON tasks "
                   "WHEN NEW.id='T1' BEGIN SELECT RAISE(ABORT,'start-rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='start-rollback'):
        start(owner)
    assert owner.runtime.task_queries.record('T1') == before
    assert execution(owner.runtime, 'T1') == old_execution


@pytest.mark.parametrize('observed', [None, 'bad-tree'])
def test_start_owner_requires_an_exact_observed_tree(project, observed):
    owner, _, root = prepare(project)
    commit_retained(root)
    restart_ready(owner)
    before = owner.runtime.task_queries.record('T1')
    old_execution = execution(owner.runtime, 'T1')
    callback = None if observed is None else lambda retained: observed
    with pytest.raises(PoiseError, match='observed|valid Git'):
        owner.runtime.task_commands.start('T1', owner.runtime.session, old_execution,
                                          restart_entry_tree=callback)
    assert owner.runtime.task_queries.record('T1') == before
    assert execution(owner.runtime, 'T1') == old_execution
