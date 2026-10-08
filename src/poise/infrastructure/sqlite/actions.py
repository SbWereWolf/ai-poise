import json
from datetime import datetime,timezone
from ...modules.foundation.errors import PoiseError,VersionConflict
from ...modules.actions.domain import ActionBinding
from ...modules.tasks.ports import TaskRepository


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class SqliteActionRepository:
    def __init__(self,db,tasks:TaskRepository):self.db,self.tasks=db,tasks

    def binding(self,task_id,stage,iteration):
        return ActionBinding(task_id,self.tasks.action_lifecycle(task_id),stage,iteration)

    def _key(self,binding):
        boundary=binding.lifecycle.boundary
        if boundary is None:
            return binding.task_id,binding.stage,binding.iteration
        stage=json.dumps([boundary,binding.stage],ensure_ascii=False,separators=(',',':'))
        return binding.task_id,stage,-binding.iteration

    def load(self,task_id,stage,iteration):
        key=self._key(self.binding(task_id,stage,iteration))
        row=self.db.execute('SELECT data FROM action_runs WHERE task_id=? AND stage=? AND iteration=?',
                            key).fetchone()
        return None if row is None else json.loads(row[0])

    def create(self,task_id,stage,iteration,value):
        key=self._key(self.binding(task_id,stage,iteration))
        self.db.execute('INSERT INTO action_runs VALUES(?,?,?,?,?)',(*key,value['version'],encode(value)))
        self._event(key,value)

    def save(self,task_id,stage,iteration,value,expected_version):
        key=self._key(self.binding(task_id,stage,iteration))
        if value['version']!=expected_version+1:raise VersionConflict('Invalid action version step')
        row=self.db.execute('UPDATE action_runs SET data=?,version=? WHERE task_id=? AND stage=? AND iteration=? AND version=?',
                            (encode(value),value['version'],*key,expected_version))
        if row.rowcount!=1:raise VersionConflict('Action state changed')
        self._event(key,value)

    def _event(self,key,value):
        self.db.execute('INSERT INTO action_events(task_id,stage,iteration,version,at,data) VALUES(?,?,?,?,?,?)',
                        (*key,value['version'],datetime.now(timezone.utc).isoformat(),encode(value)))
