"""Runtime identity/cursor storage; no Task lifecycle writes."""
import json
import uuid
from ...common import encoded,HarnessError


class RuntimeRegistry:
    def __init__(self,database):self.database=database

    def bind(self,identity,inventory):
        with self.database.transaction() as db:
            row=db.execute('SELECT session_id FROM runtime_bindings WHERE identity_key=?',(identity.key,)).fetchone()
            if row is None:
                session=identity.key if identity.kind=='external' else uuid.uuid4().hex
                db.execute('INSERT INTO runtime_bindings VALUES(?,?,?)',(identity.key,session,encoded(inventory)))
            else:
                session=row[0]
                db.execute('UPDATE runtime_bindings SET inventory=? WHERE identity_key=?',(encoded(inventory),identity.key))
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
                raise HarnessError('Transcript cursor changed; replay source without resetting it')
            db.execute('INSERT INTO runtime_cursors VALUES(?,?,?) ON CONFLICT(session_id,path) DO UPDATE SET data=excluded.data',(session,path,encoded(after)))
