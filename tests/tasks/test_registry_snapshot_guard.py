"""The real SQLite registry guard admits unchanged state and rejects drift."""
import json
from pathlib import Path
import sqlite3

import pytest

from poise.modules.foundation.errors import DomainError
from tasks.test_newborn_lifecycle import tools


FIXTURE = Path(__file__).parents[1] / 'requirements_registry/fixtures/initial_ready_contract.json'
SNAPSHOT = json.loads(FIXTURE.read_text())['snapshot']


@pytest.mark.parametrize('change', ['unchanged', 'text', 'link'])
def test_sqlite_snapshot_guard_checks_current_state_without_writing(project, change):
    runtime = tools(project, 'snapshot-guard-owner').runtime
    gate = runtime.task_commands.requirements_gate
    guard = gate.prepare_current_guard({'registry': SNAPSHOT})
    database = runtime.requirements_store.database
    if change != 'unchanged':
        with sqlite3.connect(database) as connection:
            if change == 'text':
                connection.execute('UPDATE requirements SET text=? WHERE id=?',
                                   ('Changed after preflight.', 'FIXTURE-SYSTEM'))
            else:
                connection.execute(
                    'DELETE FROM requirement_links WHERE system_id=? AND application_id=?',
                    ('FIXTURE-SYSTEM', 'FIXTURE-APPLICATION'))
    before = database.read_bytes()
    if change == 'unchanged':
        assert guard.require() is None
    else:
        with pytest.raises(DomainError) as failure:
            guard.require()
        assert str(failure.value) == 'Task requirements registry changed after creation preflight'
    assert database.read_bytes() == before
