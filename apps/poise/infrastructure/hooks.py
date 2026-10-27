"""Own hook rows composed with the existing Session transaction and SQL unit."""
from contextlib import contextmanager
import json

from domain.hooks import HookBinding
from infrastructure.sessions import SessionUnit


class HookUnit:
    def __init__(self, sessions):
        self.sessions = sessions
        self.connection = sessions.connection

    def binding(self, session_id, definition_id):
        rows = self.connection.execute(
            'SELECT id,data FROM poise_hook_bindings WHERE session_id=? ORDER BY id', (session_id,))
        for identity, raw in rows:
            if json.loads(raw).get('definition_id') == definition_id:
                return HookBinding(identity, session_id, definition_id)
        return None

    def find_event(self, binding, event_id):
        row = self.connection.execute('''SELECT seq,event,data FROM poise_hook_events
            WHERE binding_id=? AND event_id=?''', (binding.id, event_id)).fetchone()
        return None if row is None else {'id': event_id, 'sequence': row[0],
                                         'event': row[1], 'data': json.loads(row[2])}

    def create_binding(self, binding):
        self.connection.execute('INSERT INTO poise_hook_bindings(id,session_id,data) VALUES(?,?,?)',
            (binding.id, binding.session_id, json.dumps({'definition_id': binding.definition_id},
                                                       ensure_ascii=False)))

    def append_event(self, binding, event):
        cursor = self.connection.execute('''INSERT INTO poise_hook_events(binding_id,event_id,event,data)
            VALUES(?,?,?,?)''', (binding.id, event.id, event.kind,
                                 json.dumps(event.data(), ensure_ascii=False)))
        return {'id': event.id, 'sequence': cursor.lastrowid, 'event': event.kind, 'data': event.data()}


class HookRegistry:
    def __init__(self, store, sessions):
        self.store, self.sessions = store, sessions

    def observe(self, key, definition_id, event_id):
        with self.store.connection(readonly=True) as connection:
            connection.execute('BEGIN')
            try:
                unit = HookUnit(SessionUnit(connection))
                session = unit.sessions.observe(key)
                binding = None if session is None else unit.binding(session.id, definition_id)
                event = None if binding is None else unit.find_event(binding, event_id)
                return session, binding, event
            finally:
                connection.rollback()

    @contextmanager
    def transaction(self):
        with self.sessions.transaction() as sessions:
            yield HookUnit(sessions)
