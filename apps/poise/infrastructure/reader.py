"""Reader persistence composed with the existing own Session transaction."""
from contextlib import contextmanager
import json

from domain.generic import Problem
from domain.reader import ReaderState


class ReaderUnit:
    def __init__(self, connection, sessions=None):
        self.connection, self.sessions = connection, sessions

    def load(self, session_id):
        row = self.connection.execute('SELECT data FROM poise_reader_state WHERE session_id=?',
                                      (session_id,)).fetchone()
        if row is None:
            return None
        try:
            return ReaderState.restore(json.loads(row[0]))
        except (ValueError, TypeError, KeyError):
            raise Problem('state_io', {'reason': 'Invalid reader state'},
                          'configure:state', 'unavailable') from None

    def save(self, session_id, state):
        raw = json.dumps(state.data, ensure_ascii=False, separators=(',', ':'))
        self.connection.execute('''INSERT INTO poise_reader_state(session_id,data) VALUES(?,?)
            ON CONFLICT(session_id) DO UPDATE SET data=excluded.data''', (session_id, raw))


class ReaderRepository:
    def __init__(self, store, sessions):
        self.store, self.sessions = store, sessions

    def observe(self, session_id):
        with self.store.connection(readonly=True) as connection:
            return ReaderUnit(connection).load(session_id)

    @contextmanager
    def transaction(self):
        with self.sessions.transaction() as sessions:
            yield ReaderUnit(sessions.connection, sessions)
