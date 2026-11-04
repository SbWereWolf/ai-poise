"""Task-material journal records on the existing transaction/registry connection."""
import json
from ...common import encoded, PoiseError


class SqliteTaskMaterialsRepository:
    def __init__(self, connection):
        self.db = connection

    def request(self, task_id, request_id):
        row = self.db.execute(
            "SELECT event,data FROM journal WHERE task_id=? "
            "AND event IN ('task_materials.prepared','task_materials.appended') "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (task_id, request_id)).fetchone()
        return None if row is None else {**json.loads(row['data']),
                                        'committed': row['event'] == 'task_materials.appended'}

    def comments(self, task_id):
        rows = self.db.execute("SELECT data FROM journal WHERE task_id=? "
            "AND event='task_materials.appended' ORDER BY seq", (task_id,))
        return [comment for row in rows
                for comment in json.loads(row['data'])['result']['comments']]

    def _append(self, task_id, event, value):
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (value['at'], value['actor'], task_id, event, encoded(value)))

    def prepare(self, task_id, value):
        if self.request(task_id, value['request_id']) is not None:
            raise PoiseError('Task material preparation already exists; retry original request')
        self._append(task_id, 'task_materials.prepared', value)

    def commit(self, task_id, value):
        self._append(task_id, 'task_materials.appended', value)
