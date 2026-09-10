import json
from ...common import encoded,HarnessError


class SqliteHandoffRepository:
    def __init__(self,db):self.db=db
    def get(self,actor,request_id):
        row=self.db.execute('SELECT data FROM handoffs WHERE actor=? AND request_id=?',(actor,request_id)).fetchone()
        return None if row is None else json.loads(row[0])
    def latest(self,task_id):
        row=self.db.execute("SELECT data FROM handoffs WHERE task_id=? AND state='released' ORDER BY seq DESC LIMIT 1",(task_id,)).fetchone()
        return None if row is None else json.loads(row[0])
    def insert(self,record):
        self.db.execute('INSERT INTO handoffs(actor,request_id,task_id,state,data) VALUES(?,?,?,?,?)',
            (record['actor'],record['request_id'],record['task_id'],record['state'],encoded(record)))
    def replace(self,record):
        self.db.execute('UPDATE handoffs SET state=?,data=? WHERE actor=? AND request_id=?',
                        (record['state'],encoded(record),record['actor'],record['request_id']))
    def unbind(self,actor,task_id):
        r=self.db.execute('UPDATE sessions SET task_id=NULL WHERE id=? AND task_id=?',(actor,task_id))
        if r.rowcount!=1:raise HarnessError('Session no longer owns this binding')
    def bind(self,actor,task_id):
        self.db.execute('INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id',(actor,task_id))
