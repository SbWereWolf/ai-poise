"""Immutable duplicate-registration facts in the existing owned history."""
from dataclasses import asdict
import json

from apps.sequencing.infrastructure.ownership_store import OwnershipRepository

REGISTRATION_EVENT = 'task.duplicate_registered'


class DuplicateRepository:
    def __init__(self, connection):
        self.connection = connection
        self.ownership = OwnershipRepository(connection)

    def registration(self, task_id):
        row = self.connection.execute(
            'SELECT data FROM sequencing_history WHERE task_id=? AND event=? ORDER BY seq LIMIT 1',
            (task_id, REGISTRATION_EVENT)).fetchone()
        return None if row is None else json.loads(row['data'])

    def task_exists(self, task_id):
        return self.connection.execute('SELECT 1 FROM tasks WHERE task_id=?', (task_id,)).fetchone() is not None

    def family_ids(self, task_id):
        self.ownership.task(task_id)
        saved = self.registration(task_id)
        parent = task_id if saved is None else saved['identity']['parent_id']
        children = [row['task_id'] for row in self.connection.execute(
            "SELECT task_id FROM sequencing_history WHERE event=? "
            "AND json_extract(data,'$.identity.parent_id')=? ORDER BY task_id",
            (REGISTRATION_EVENT, parent))]
        if parent == task_id and not children:
            return None
        return {'parent_id': parent,
                'members': [{'task_id': identifier} for identifier in sorted([parent, *children])]}

    def record(self, identity, result, claimant, at):
        self.ownership.record(result['task_id'], claimant, REGISTRATION_EVENT,
                              {'identity': asdict(identity), 'result': result}, at)
