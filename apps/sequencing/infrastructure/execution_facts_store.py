"""Immutable received facts and owned state on a caller-owned SQLite connection."""
import json

from apps.sequencing.infrastructure.ownership_store import OwnershipRepository
from apps.sequencing.infrastructure.workspace_store import WorkspaceRepository

LABEL_EVENT = 'execution.stage_label_recorded'
OUTCOME_EVENT = 'execution.outcome_recorded'


class ExecutionFactsRepository:
    def __init__(self, connection):
        self.connection = connection
        self.ownership = OwnershipRepository(connection)
        self.workspace = WorkspaceRepository(connection)

    def _find(self, event, path, identity):
        row = self.connection.execute(
            'SELECT data FROM sequencing_history WHERE event=? AND json_extract(data,?)=? '
            'ORDER BY seq LIMIT 1', (event, path, identity)).fetchone()
        return None if row is None else json.loads(row['data'])

    def label(self, label_id):
        return self._find(LABEL_EVENT, '$.fact.label_id', label_id)

    def outcome(self, outcome_id):
        return self._find(OUTCOME_EVENT, '$.fact.outcome_id', outcome_id)

    def _latest(self, task_id, event):
        row = self.connection.execute(
            'SELECT data FROM sequencing_history WHERE task_id=? AND event=? '
            'ORDER BY seq DESC LIMIT 1', (task_id, event)).fetchone()
        return None if row is None else json.loads(row['data'])['fact']

    def value(self, task_id):
        self.ownership.task(task_id)
        return {'task_id': task_id, 'stage_label': self._latest(task_id, LABEL_EVENT),
                'outcome': self._latest(task_id, OUTCOME_EVENT)}

    def record_label(self, fact, result, actor, at):
        self.ownership.record(fact['task_id'], actor, LABEL_EVENT,
                              {'fact': fact, 'result': result}, at)

    def record_outcome(self, fact, result, actor, at):
        self.ownership.record(fact['task_id'], actor, OUTCOME_EVENT,
                              {'fact': fact, 'result': result}, at)

    def set_outcome(self, task_id, state, terminal):
        encoded = None if terminal is None else json.dumps(
            terminal, ensure_ascii=False, separators=(',', ':'))
        self.connection.execute('UPDATE tasks SET state=?,terminal_json=? WHERE task_id=?',
                                (state, encoded, task_id))
