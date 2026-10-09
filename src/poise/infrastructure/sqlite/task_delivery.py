"""Compact delivery journal authority; never stores result/proof bytes."""
import json
from datetime import datetime, timezone
from ...common import encoded, PoiseError
from ...modules.task_delivery.domain import DeliveryState


class SqliteTaskDeliveryRepository:
    def __init__(self, connection):
        self.db = connection

    def read(self, task_id):
        row = self.db.execute("SELECT data FROM journal WHERE task_id=? AND event='task_delivery.state' ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone()
        return None if row is None else DeliveryState.restore(json.loads(row[0]))

    def agreement_request(self, task_id, request_id, intent_digest):
        rows = self.db.execute("SELECT data FROM journal WHERE task_id=? AND event='task_delivery.state' AND json_extract(data,'$.agreement.request_id')=? ORDER BY seq LIMIT 1", (task_id, request_id))
        row = rows.fetchone()
        if row is None:
            return None
        state = DeliveryState.restore(json.loads(row[0]))
        if state.data['intent_digest'] != intent_digest:
            raise PoiseError('Delivery request identity conflicts with saved intent digest')
        return state.result(replayed=True)

    def save(self, actor, state, expected_revision):
        previous = self.read(state.data['task'])
        actual = -1 if previous is None else previous.data['revision']
        if actual != expected_revision or state.data['revision'] != expected_revision + 1:
            raise PoiseError('Delivery revision changed concurrently')
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), actor, state.data['task'],
                         'task_delivery.state', encoded(state.data)))
