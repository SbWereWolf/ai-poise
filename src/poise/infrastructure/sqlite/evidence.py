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

    def timeout_history(self, profile):
        durations=[]
        evidence_ids=[]
        for row in self.db.execute('SELECT data FROM evidence ORDER BY rowid'):
            receipt=json.loads(row[0])
            if receipt.get('timeout_profile') != profile:
                continue
            if receipt.get('passed') is not True or receipt.get('timed_out') or receipt.get('cancelled'):
                continue
            duration=receipt.get('duration_seconds')
            if isinstance(duration, bool) or not isinstance(duration, (int,float)) or duration < 0:
                continue
            durations.append(float(duration))
            evidence_ids.append(receipt['id'])
        return {'durations':durations,'evidence_ids':evidence_ids}

    @staticmethod
    def observe_runs(path, run_ids, max_bytes):
        """Return only cancellation facts from exact immutable evidence IDs."""
        from ..diagnostic_io import readonly_database, json_record
        result = []
        with readonly_database(path) as db:
            for run_id in run_ids:
                row = db.execute('SELECT task_id,stage,iteration,substr(data,1,?) AS data FROM evidence WHERE id=?', (max_bytes+1, run_id)).fetchone()
                if row is None:
                    result.append({'id': run_id, 'status': 'not_observed'})
                    continue
                receipt = json_record(row['data'], max_bytes)
                if not isinstance(receipt, dict) or receipt.get('id') != run_id:
                    raise PoiseError('Evidence receipt identity mismatch')
                result.append({'id': run_id, 'status': 'observed', 'task_id': row['task_id'],
                               'stage': row['stage'], 'iteration': row['iteration'],
                               'actual_exit_code': receipt.get('actual_exit_code'),
                               'timed_out': receipt.get('timed_out'),
                               'cancelled': receipt.get('cancelled'),
                               'cancellation_reason': receipt.get('cancellation_reason'),
                               'cancellation_observation': receipt.get('cancellation_observation')})
        return result
