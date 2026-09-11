"""Receipt persistence. No predicate evaluation or lifecycle transitions."""
import json
from ...modules.foundation.errors import PoiseError


class SqliteEvidenceRepository:
    def __init__(self, connection):
        self.db=connection

    def record(self, task_id, stage, iteration, receipt):
        encoded=json.dumps(receipt,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        prior=self.db.execute('SELECT task_id,stage,iteration,data FROM evidence WHERE id=?',(receipt['id'],)).fetchone()
        if prior is not None:
            if tuple(prior) != (task_id,stage,iteration,encoded):
                raise PoiseError('Receipt identity conflict: immutable evidence cannot be replaced')
            return
        self.db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(receipt['id'],task_id,stage,iteration,encoded))

    def list_for(self, task_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM evidence WHERE task_id=? ORDER BY rowid',(task_id,))]
