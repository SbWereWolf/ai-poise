"""Compact delivery journal authority; never stores result/proof bytes."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from ...common import encoded, PoiseError
from ...modules.task_delivery.domain import DeliveryState, MaterialUseState


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

    def read_use(self, task_id, key):
        row = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='task_delivery.material_use' "
            "AND json_extract(data,'$.key')=? ORDER BY seq DESC LIMIT 1", (task_id, key)).fetchone()
        return None if row is None else MaterialUseState.restore(json.loads(row[0]))

    def _save_use(self, actor, value, expected_revision):
        state = MaterialUseState.restore(value)
        previous = self.read_use(value['source'], value['key'])
        actual = -1 if previous is None else previous.data['revision']
        if actual != expected_revision or value['revision'] != expected_revision + 1:
            raise PoiseError('Material use revision changed concurrently')
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), actor, value['source'],
                         'task_delivery.material_use', encoded(state.data)))

    def admit_use(self, actor, intent, source, attempt, version, epoch):
        previous = self.read_use(source, intent.key())
        if previous is not None and previous.data['intent'] != intent.__dict__:
            raise PoiseError('Material request identity conflicts with saved intent')
        if previous is not None and previous.data['outcome'] == 'accepted':
            raise PoiseError('Material operation already accepted; replay its original result')
        revision = -1 if previous is None else previous.data['revision']
        attempts = [] if previous is None else previous.data['attempts']
        value = {'schema': 'task-material-use-1', 'key': intent.key(), 'revision': revision + 1,
                 'intent': intent.__dict__, 'source': source, 'task_version': version,
                 'epoch': epoch, 'attempts': attempts + [attempt], 'outcome': None, 'actor': actor}
        self._save_use(actor, value, revision)

    def complete_use(self, actor, token, *, accepted):
        for source in token.sources:
            previous = self.read_use(source, token.intent.key())
            if previous is None or token.attempt not in previous.data['attempts']:
                continue
            value = deepcopy(previous.data)
            # A durable accepted original outcome makes all its attempts replayable.
            # A refusal closes this attempt only, never another unfinished request.
            value['attempts'] = [] if accepted else [a for a in value['attempts'] if a != token.attempt]
            value['outcome'] = 'accepted' if accepted else ('refused' if not value['attempts'] else None)
            value['revision'] += 1
            value['actor'] = actor
            self._save_use(actor, value, previous.data['revision'])

    def pending_uses(self, task_id):
        rows = self.db.execute(
            "SELECT j.data FROM journal j WHERE j.task_id=? AND j.event='task_delivery.material_use' "
            "AND j.seq=(SELECT MAX(n.seq) FROM journal n WHERE n.task_id=j.task_id "
            "AND n.event=j.event AND json_extract(n.data,'$.key')=json_extract(j.data,'$.key'))", (task_id,))
        return any(MaterialUseState.restore(json.loads(row[0])).data['attempts'] for row in rows)
