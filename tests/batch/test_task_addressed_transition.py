"""Public Work read preserves the existing Task stage-contract transition refusal."""
from contextlib import closing
import json
import sqlite3

import pytest

from poise.modules.foundation.errors import DomainError
from .task_addressed_support import encode, packet, read_tools, seed_scene, snapshot


def test_addressed_read_preserves_missing_stage_contract_refusal_without_writes(project):
    path, _ = seed_scene(project)
    with closing(sqlite3.connect(path)) as db:
        row = db.execute('SELECT metadata FROM tasks WHERE id=?', ('READ-ALPHA',)).fetchone()
        value = json.loads(row[0])
        value['contract'].pop('stage_contracts')
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (encode(value), 'READ-ALPHA'))
        db.commit()
    tools = read_tools(project)
    before = snapshot(path)
    try:
        with pytest.raises(DomainError, match=(
                '^stage_contract_transition_required: initialize_stage_contracts is required$')):
            tools.invoke(packet())
    finally:
        assert snapshot(path) == before
