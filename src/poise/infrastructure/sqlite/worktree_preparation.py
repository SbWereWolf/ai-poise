"""Preparation-owned journal events in the existing Task transaction."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from ...common import PoiseError, encoded
from ...modules.worktree_preparation.domain import PreparationAttempt


class SqlitePreparationRepository:
    def __init__(self, connection):
        self.db = connection

    def _read(self, task_id, request_id=None):
        sql = "SELECT data FROM journal WHERE task_id=? AND event='worktree_preparation.state'"
        arguments = [task_id]
        if request_id is not None:
            sql += " AND json_extract(data,'$.request_id')=?"
            arguments.append(request_id)
        row = self.db.execute(sql + ' ORDER BY seq DESC LIMIT 1', arguments).fetchone()
        return None if row is None else PreparationAttempt(**json.loads(row['data']))

    def latest(self, task_id):
        return self._read(task_id)

    def by_request(self, task_id, request_id):
        return self._read(task_id, request_id)

    def save(self, actor, state, expected_revision):
        task_id = state.coordinates['task_id']
        owner = self.db.execute('SELECT claimed_by FROM tasks WHERE id=?', (task_id,)).fetchone()
        if owner is None or owner['claimed_by'] != actor:
            raise PoiseError('Preparation requires the current Task owner')
        latest = self.latest(task_id)
        revision = 0 if latest is None else latest.revision
        if revision != expected_revision:
            raise PoiseError('Preparation state revision conflict')
        saved = replace(state, revision=revision + 1)
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), actor, task_id,
                         'worktree_preparation.state', encoded(asdict(saved))))
        return saved
