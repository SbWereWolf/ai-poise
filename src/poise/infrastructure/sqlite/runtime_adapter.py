"""Runtime identity/cursor storage; no Task lifecycle writes."""
import json
import sqlite3
from ...common import encoded,PoiseError


class RuntimeRegistry:
    def __init__(self,database):self.database=database

    def existing(self,identity_key,inventory):
        with self.database.transaction() as db:
            row=db.execute('SELECT session_id FROM runtime_bindings WHERE identity_key=?',(identity_key,)).fetchone()
            if row is None:return None
            db.execute('UPDATE runtime_bindings SET inventory=? WHERE identity_key=?',(encoded(inventory),identity_key))
            return row[0]

    def reserve(self,identity_key,session,inventory):
        with self.database.transaction() as db:
            row=db.execute('SELECT session_id FROM runtime_bindings WHERE identity_key=?',(identity_key,)).fetchone()
            if row is not None:
                db.execute('UPDATE runtime_bindings SET inventory=? WHERE identity_key=?',(encoded(inventory),identity_key))
                return row[0]
            try:
                db.execute('INSERT INTO runtime_bindings VALUES(?,?,?)',(identity_key,session,encoded(inventory)))
            except sqlite3.IntegrityError:
                return None
            return session

    def cursor(self,session,path):
        with self.database.transaction() as db:
            row=db.execute('SELECT data FROM runtime_cursors WHERE session_id=? AND path=?',(session,path)).fetchone()
            return None if row is None else json.loads(row[0])

    def save_cursor(self,session,path,before,after):
        with self.database.transaction() as db:
            row=db.execute('SELECT data FROM runtime_cursors WHERE session_id=? AND path=?',(session,path)).fetchone()
            previous=None if row is None else json.loads(row[0])
            if previous!=before:
                if previous==after:return
                raise PoiseError('Transcript cursor changed; replay source without resetting it')
            db.execute('INSERT INTO runtime_cursors VALUES(?,?,?) ON CONFLICT(session_id,path) DO UPDATE SET data=excluded.data',(session,path,encoded(after)))
