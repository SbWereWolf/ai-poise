import json
from datetime import datetime,timezone
from ...modules.foundation.errors import HarnessError,VersionConflict


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class SqliteActionRepository:
    def __init__(self,db):self.db=db

    def load(self,task_id,stage,iteration):
        row=self.db.execute('SELECT data FROM action_runs WHERE task_id=? AND stage=? AND iteration=?',
                            (task_id,stage,iteration)).fetchone()
        return None if row is None else json.loads(row[0])

    def create(self,task_id,stage,iteration,value):
        self.db.execute('INSERT INTO action_runs VALUES(?,?,?,?,?)',(task_id,stage,iteration,value['version'],encode(value)))
        self._event(task_id,stage,iteration,value)

    def save(self,task_id,stage,iteration,value,expected_version):
        if value['version']!=expected_version+1:raise VersionConflict('Invalid action version step')
        row=self.db.execute('UPDATE action_runs SET data=?,version=? WHERE task_id=? AND stage=? AND iteration=? AND version=?',
                            (encode(value),value['version'],task_id,stage,iteration,expected_version))
        if row.rowcount!=1:raise VersionConflict('Action state changed')
        self._event(task_id,stage,iteration,value)

    def _event(self,task_id,stage,iteration,value):
        self.db.execute('INSERT INTO action_events(task_id,stage,iteration,version,at,data) VALUES(?,?,?,?,?,?)',
                        (task_id,stage,iteration,value['version'],datetime.now(timezone.utc).isoformat(),encode(value)))
