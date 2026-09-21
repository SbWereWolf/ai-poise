"""Integration cleanup reconciles only a proven obsolete dependent binding."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from .helpers import integration_input, prepare_completed_task, request, source_change


def seed_legacy_binding(runtime):
    # Model a persisted pre-fix binding in this test's isolated database only.
    with runtime.store.transaction() as db:
        db.execute("UPDATE sessions SET task_id=NULL WHERE task_id='T1'")
        db.execute("INSERT INTO sessions(id,task_id) VALUES(?,?) "
                   "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id",
                   ('predecessor', 'T1'))


def binding_and_audit(runtime):
    with runtime.store.transaction() as db:
        binding = db.execute("SELECT task_id FROM sessions WHERE id='predecessor'").fetchone()[0]
        entries = db.execute(
            "SELECT session_id,task_id,data FROM journal "
            "WHERE event='ownership.integrated_worktree_reconciled' ORDER BY seq").fetchall()
    return binding, [(row[0], row[1], json.loads(row[2])) for row in entries]


def saved_result(runtime):
    with runtime.store.transaction() as db:
        task = tuple(db.execute(
            "SELECT status,version,claimed_by,current_submission_id,metadata "
            "FROM tasks WHERE id='T1'").fetchone())
        execution = tuple(db.execute(
            "SELECT version,data FROM task_execution WHERE task_id='T1'").fetchone())
    return task, execution


def completed_integration(project):
    tools, worktree, source = prepare_completed_task(project, source_change)
    packet = request('integrate', integration_input(project, source))
    result = tools.invoke(packet)
    assert result['status'] == 'integrated'
    assert not worktree.exists()
    return tools, worktree, packet, result


@pytest.mark.parametrize('legacy_phase', ['before_cleanup', 'after_cleanup'])
def test_integration_reconciles_legacy_binding_once_and_preserves_results(project, legacy_phase):
    tools, worktree, source = prepare_completed_task(project, source_change)
    packet = request('integrate', integration_input(project, source))
    if legacy_phase == 'before_cleanup':
        seed_legacy_binding(tools.runtime)
    result = tools.invoke(packet)
    assert result['status'] == 'integrated'
    assert not worktree.exists()
    if legacy_phase == 'after_cleanup':
        seed_legacy_binding(tools.runtime)
    before = saved_result(tools.runtime)
    resumer = WorkTools(WorkPoise(project['config_path'], 'reconciler'))
    replay = resumer.invoke(packet)
    assert replay == {**result, 'replayed': True}
    binding, audit = binding_and_audit(resumer.runtime)
    assert binding is None
    assert len(audit) == 1
    assert audit[0][0] == (tools.runtime.session if legacy_phase == 'before_cleanup'
                           else resumer.runtime.session)
    assert audit[0][1] == 'T1'
    assert audit[0][2]['released_owner'] == 'predecessor'
    assert audit[0][2]['request_id'] == 'integrate-1'
    assert audit[0][2]['accepted_commit'] == source
    assert saved_result(resumer.runtime) == before
    assert resumer.invoke(packet) == replay
    assert binding_and_audit(resumer.runtime) == (binding, audit)
    assert git(project['app'], 'rev-parse', 'HEAD') == result['target_after']
    assert git(project['app'], 'branch', '--list', 'tasks/T1') == ''


@pytest.mark.parametrize('resource', ['worktree', 'branch', 'backup', 'symlink', 'backup_symlink', 'backup_parent_symlink'])
def test_recreated_resource_blocks_metadata_reconciliation_without_deletion(project, resource):
    tools, worktree, packet, result = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    marker = None
    if resource == 'branch':
        git(project['app'], 'branch', 'tasks/T1', result['target_after'])
    elif resource in ('backup_symlink', 'backup_parent_symlink'):
        directory = Path(result['temporary_backup_directory'])
        if resource == 'backup_parent_symlink':
            directory = directory.parent
            if directory.exists():
                directory.rmdir()
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.symlink_to(directory.parent / 'absent-target', target_is_directory=True)
    elif resource == 'symlink':
        worktree.symlink_to(worktree.parent / 'absent-target', target_is_directory=True)
    else:
        directory = worktree if resource == 'worktree' else Path(result['temporary_backup_directory'])
        directory.mkdir(parents=True)
        marker = directory / 'foreign.txt'
        marker.write_text('Preserve recreated resource.\n')
    before = saved_result(tools.runtime)
    with pytest.raises(PoiseError, match='[Rr]esource|[Ww]orktree|[Bb]ranch|[Bb]ackup'):
        tools.invoke(packet)
    assert saved_result(tools.runtime) == before
    assert binding_and_audit(tools.runtime) == ('T1', [])
    if marker is not None:
        assert marker.read_text() == 'Preserve recreated resource.\n'
    if resource == 'branch':
        assert git(project['app'], 'rev-parse', 'refs/heads/tasks/T1') == result['target_after']
    if resource == 'symlink':
        assert worktree.is_symlink()
    if resource in ('backup_symlink', 'backup_parent_symlink'):
        assert directory.is_symlink()


@pytest.mark.parametrize('guard', ['independent', 'claimed', 'unconfirmed', 'incomplete', 'publication_commit', 'accepted_commit', 'worktree_pointer', 'backup_pointer'])
def test_reconciliation_preserves_binding_without_completed_dependent_proof(project, guard):
    tools, _, packet, _ = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    with tools.runtime.store.transaction() as db:
        if guard == 'independent':
            metadata = json.loads(db.execute("SELECT metadata FROM tasks WHERE id='T1'").fetchone()[0])
            metadata['process']['worktree_required'] = False
            db.execute("UPDATE tasks SET metadata=? WHERE id='T1'", (json.dumps(metadata),))
        elif guard == 'claimed':
            db.execute("UPDATE tasks SET claimed_by=? WHERE id='T1'", ('foreign-worker',))
        else:
            data = json.loads(db.execute("SELECT data FROM task_execution WHERE task_id='T1'").fetchone()[0])
            if guard == 'unconfirmed':
                data['pending']['publication'] = None
            elif guard == 'incomplete':
                data['pending']['cleanup']['task_branch'] = 'pending'
            elif guard == 'publication_commit':
                data['pending']['publication']['commit'] = '0' * 40
            elif guard == 'accepted_commit':
                data['last_report']['commit'] = '0' * 40
            elif guard == 'worktree_pointer':
                data['worktree'] += '-changed'
            elif guard == 'backup_pointer':
                data['pending']['temporary_backup_directory'] += '-changed'
            db.execute("UPDATE task_execution SET data=? WHERE task_id='T1'", (json.dumps(data),))
    before = saved_result(tools.runtime)
    if guard == 'independent':
        assert tools.invoke(packet)['status'] == 'integrated'
    else:
        with pytest.raises(PoiseError, match='[Cc]leanup|[Oo]wnership|[Pp]ublication|[Tt]erminal'):
            tools.invoke(packet)
    assert binding_and_audit(tools.runtime) == ('T1', [])
    assert saved_result(tools.runtime) == before


def test_reconciliation_and_audit_roll_back_together(project, monkeypatch):
    from poise.infrastructure.sqlite.ownership import SqliteOwnershipRepository

    tools, _, packet, _ = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    before = saved_result(tools.runtime)
    original = SqliteOwnershipRepository.bind_worktree

    def fail_after_release(self, actor, task_id):
        original(self, actor, task_id)
        if actor == 'predecessor' and task_id is None:
            raise RuntimeError('injected after binding release')

    monkeypatch.setattr(SqliteOwnershipRepository, 'bind_worktree', fail_after_release)
    with pytest.raises(RuntimeError, match='injected after binding release'):
        tools.invoke(packet)
    assert binding_and_audit(tools.runtime) == ('T1', [])
    assert saved_result(tools.runtime) == before


def test_audit_insert_failure_rolls_back_reconciliation(project):
    import sqlite3

    tools, _, packet, _ = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    before = saved_result(tools.runtime)
    with tools.runtime.store.transaction() as db:
        db.executescript("""
            CREATE TRIGGER reject_ownership_audit BEFORE INSERT ON journal
            WHEN NEW.event='ownership.integrated_worktree_reconciled'
            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError, match='injected audit failure'):
        tools.invoke(packet)
    assert binding_and_audit(tools.runtime) == ('T1', [])
    assert saved_result(tools.runtime) == before


def test_rewritten_target_history_blocks_binding_release(project):
    tools, _, packet, result = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    before = saved_result(tools.runtime)
    # Deliberately rewrite only this isolated test repository to a pre-publication head.
    git(project['app'], 'reset', '--hard', packet['input']['expected_target_commit'])
    with pytest.raises(PoiseError, match='Publication'):
        tools.invoke(packet)
    assert saved_result(tools.runtime) == before
    assert binding_and_audit(tools.runtime) == ('T1', [])
    assert git(project['app'], 'rev-parse', 'HEAD') == packet['input']['expected_target_commit']


def test_unreadable_cleanup_path_is_not_absence(project, monkeypatch):
    tools, _, packet, result = completed_integration(project)
    seed_legacy_binding(tools.runtime)
    before = saved_result(tools.runtime)
    backup = Path(result['temporary_backup_directory'])
    original = Path.lstat

    def deny_backup(path):
        if path == backup:
            raise PermissionError('injected unreadable backup path')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', deny_backup)
    with pytest.raises(PoiseError, match='Backup.*verified'):
        tools.invoke(packet)
    assert saved_result(tools.runtime) == before
    assert binding_and_audit(tools.runtime) == ('T1', [])


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_terminal_binding_is_never_recovered_through_after_crash(project, status):
    tools, _, _ = prepare_completed_task(project, source_change, accept=status == 'completed')
    if status == 'cancelled':
        assert tools.invoke(request('cancel', {'reason': 'Cancel this isolated test task.'}))['status'] == status
    seed_legacy_binding(tools.runtime)
    observer = WorkTools(WorkPoise(project['config_path'], 'terminal-observer'))
    view = observer.invoke(request('show', {'queries': [
        {'id': 'recovery', 'kind': 'ownership_recovery', 'task_ids': ['T1']},
    ]}))['results'][0]['value']
    packet = view['recovery_template']
    packet['input'].update(
        reason='Explicit isolated test of the unchanged terminal guard.',
        writers_stopped=True,
        authorization={'role': 'user', 'decision': 'User authorizes crash recovery in this fixture.'},
    )
    before = saved_result(tools.runtime)
    with pytest.raises(PoiseError, match='terminal Task ownership'):
        observer.invoke(packet)
    assert saved_result(tools.runtime) == before
    assert binding_and_audit(tools.runtime) == ('T1', [])
