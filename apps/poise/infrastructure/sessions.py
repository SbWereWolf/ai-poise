"""Own format1 Session repository and short application-selected transactions."""
from contextlib import contextmanager
import json

from domain.sessions import Session


class SessionUnit:
    def __init__(self, connection):
        self.connection = connection

    def observe(self, key):
        row = self.connection.execute('''
            SELECT s.id,s.origin,s.status,s.revision,r.role,r.data
            FROM poise_bindings b JOIN poise_sessions s ON s.id=b.session_id
            LEFT JOIN poise_role_assignments r ON r.session_id=s.id
            WHERE b.identity_key=?''', (key,)).fetchone()
        if row is None:
            return None
        return Session(*row[:5], None if row[5] is None else json.loads(row[5]))

    def exists(self, identity):
        return self.connection.execute('SELECT id FROM poise_sessions WHERE id=?',
                                       (identity,)).fetchone() is not None

    def create(self, key, session):
        self.connection.execute('INSERT INTO poise_sessions(id,origin,status,revision) VALUES(?,?,?,?)',
                                (session.id, session.origin, session.status, session.revision))
        self.connection.execute('INSERT INTO poise_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                                (key, session.id, '[]'))

    def assign(self, session):
        self.connection.execute('INSERT INTO poise_role_assignments(session_id,role,data) VALUES(?,?,?)',
                                (session.id, session.role, json.dumps(session.role_assignment,
                                 ensure_ascii=False, separators=(',', ':'))))
        self.connection.execute('UPDATE poise_sessions SET revision=? WHERE id=?',
                                (session.revision, session.id))


class SessionRepository:
    def __init__(self, store):
        self.store = store

    def observe(self, key):
        with self.store.connection(readonly=True) as connection:
            return SessionUnit(connection).observe(key)

    @contextmanager
    def transaction(self):
        with self.store.connection(readonly=False) as connection:
            try:
                connection.execute('BEGIN IMMEDIATE')
                yield SessionUnit(connection)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
