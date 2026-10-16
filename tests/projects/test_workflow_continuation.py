"""Current execution admission uses current facts, not historical receipts."""
import pytest
from pathlib import Path

from poise.application.tasks import require_start_execution
from poise.modules.foundation.errors import DomainError


def test_retained_current_execution_does_not_require_historical_receipt():
    current = {'worktree': '/selected/tasks/T1', 'pending': None}

    require_start_execution(current)


def test_pending_current_execution_still_requires_recovery():
    current = {'worktree': '/selected/tasks/T1', 'pending': {'kind': 'unknown-outcome'}}

    with pytest.raises(DomainError, match='pending'):
        require_start_execution(current)


def test_absent_current_execution_needs_no_historical_receipt():
    require_start_execution(None)


@pytest.mark.parametrize('pending,expected_ids', [(None, ['T1']), ({'kind': 'unknown-outcome'}, [])])
def test_actual_discovery_uses_current_execution_without_historical_verifier(project, monkeypatch, pending, expected_ids):
    import json
    import sqlite3
    from conftest import DeterministicClock
    from poise.runtime import Poise
    from poise.infrastructure.project_availability import ReadOnlyProjectAvailability
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository

    runtime = Poise(project['config_path'], 'workflow-discovery-test', DeterministicClock())
    runtime.bootstrap(json.loads(project['task_path'].read_text()))
    with sqlite3.connect(runtime.store.path) as db:
        execution = json.loads(db.execute('SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        execution['pending'] = pending
        db.execute('UPDATE task_execution SET data=? WHERE task_id=?', (json.dumps(execution), 'T1'))
        db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?', ('available', 'T1'))

    def historical_verifier_is_not_current_admission(*args):
        raise AssertionError('Current execution must not request historical migration provenance')

    monkeypatch.setattr(SqliteTaskRepository, 'migrated_start_context', historical_verifier_is_not_current_admission, raising=False)
    selected = {'project': project['cfg']['project'], 'config_path': str(project['config_path'])}
    with sqlite3.connect(runtime.store.path) as db:
        before = db.execute('SELECT status,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone()

    observed = ReadOnlyProjectAvailability().startable(selected)

    assert [row['task'] for row in observed] == expected_ids
    with sqlite3.connect(runtime.store.path) as db:
        assert db.execute('SELECT status,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone() == before


@pytest.mark.parametrize('pending', [None, {'kind': 'unknown-outcome'}])
def test_real_start_keeps_current_execution_without_historical_verifier(project, monkeypatch, pending):
    import json
    import sqlite3
    from conftest import DeterministicClock
    from poise.runtime import Poise
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository

    runtime = Poise(project['config_path'], 'workflow-start-test', DeterministicClock())
    runtime.bootstrap(json.loads(project['task_path'].read_text()))
    with sqlite3.connect(runtime.store.path) as db:
        execution = json.loads(db.execute('SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        execution['pending'] = pending
        db.execute('UPDATE task_execution SET data=? WHERE task_id=?', (json.dumps(execution), 'T1'))
        db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?', ('available', 'T1'))
        before_task = db.execute('SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
        before_execution = db.execute('SELECT data,version FROM task_execution WHERE task_id=?', ('T1',)).fetchone()
        before_sessions = db.execute('SELECT id,task_id FROM sessions ORDER BY id').fetchall()
    tree = runtime._git(Path(execution['worktree']), 'rev-parse', 'HEAD^{tree}')

    def forbidden_history(*args):
        raise AssertionError('Real start must not request historical migration provenance')

    monkeypatch.setattr(SqliteTaskRepository, 'migrated_start_context', forbidden_history, raising=False)
    if pending is None:
        runtime.task_commands.start('T1', runtime.session, execution, restart_entry_tree=lambda current: tree)
    else:
        with pytest.raises(DomainError, match='pending'):
            runtime.task_commands.start('T1', runtime.session, execution, restart_entry_tree=lambda current: tree)
    with sqlite3.connect(runtime.store.path) as db:
        after_task = db.execute('SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
        after_execution = db.execute('SELECT data,version FROM task_execution WHERE task_id=?', ('T1',)).fetchone()
        if pending is None:
            assert after_task[0] == 'active'
            assert after_task[1:4] == before_task[1:4]
            assert after_task[4] == before_task[4] + 2
            assert after_task[5] == before_task[5]
            assert json.loads(after_execution[0]) == {**execution, 'entry_tree': tree}
            assert after_execution[1] == before_execution[1] + 1
        else:
            assert after_task == before_task
            assert after_execution == before_execution
        assert db.execute('SELECT id,task_id FROM sessions ORDER BY id').fetchall() == before_sessions
