"""Receipt persistence. No predicate evaluation or lifecycle transitions."""
import json
from ...modules.foundation.errors import PoiseError


class SqliteEvidenceRepository:
    def __init__(self, connection):
        self.db=connection

    @staticmethod
    def prepare(receipt):
        return json.dumps(receipt,sort_keys=True,ensure_ascii=False,separators=(',',':'))

    def record(self, task_id, stage, iteration, receipt):
        self.record_prepared(task_id, stage, iteration, receipt['id'], self.prepare(receipt))

    def record_prepared(self, task_id, stage, iteration, receipt_id, encoded):
        prior=self.db.execute('SELECT task_id,stage,iteration,data FROM evidence WHERE id=?',(receipt_id,)).fetchone()
        if prior is not None:
            if tuple(prior) != (task_id,stage,iteration,encoded):
                raise PoiseError('Receipt identity conflict: immutable evidence cannot be replaced')
            return
        self.db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(receipt_id,task_id,stage,iteration,encoded))

    @staticmethod
    def capture(db, task_id):
        records = tuple(r[0] for r in db.execute('SELECT data FROM evidence WHERE task_id=? ORDER BY rowid',(task_id,)))
        locations = db.execute('SELECT data FROM transfer_locations WHERE task_id=?', (task_id,)).fetchone()
        return records, None if locations is None else locations[0]

    @staticmethod
    def restore(capture):
        raw, locations = capture
        records = [json.loads(row) for row in raw]
        if locations is not None:
            from ..material_locations import relocate_receipt
            parsed = json.loads(locations)
            records = [relocate_receipt(record, parsed) for record in records]
        return records

    def list_for(self, task_id):
        return self.restore(self.capture(self.db, task_id))

    @staticmethod
    def capture_timeout_history(db):
        return tuple(row[0] for row in db.execute('SELECT data FROM evidence ORDER BY rowid'))

    def timeout_history(self, profile):
        return self.restore_timeout_history(self.capture_timeout_history(self.db), profile)

    @staticmethod
    def restore_timeout_history(rows, profile):
        durations=[]
        evidence_ids=[]
        for row in rows:
            receipt=json.loads(row)
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
        with readonly_database(path) as db:
            rows = [(run_id, db.execute('SELECT task_id,stage,iteration,substr(data,1,?) AS data '
                'FROM evidence WHERE id=?', (max_bytes+1, run_id)).fetchone()) for run_id in run_ids]
        result = []
        for run_id, row in rows:
            if row is None:
                result.append({'id':run_id, 'status':'not_observed'})
                continue
            receipt = json_record(row['data'], max_bytes)
            if not isinstance(receipt, dict) or receipt.get('id') != run_id:
                raise PoiseError('Evidence receipt identity mismatch')
            result.append({'id':run_id, 'status':'observed', 'task_id':row['task_id'],
                'stage':row['stage'], 'iteration':row['iteration'],
                'actual_exit_code':receipt.get('actual_exit_code'), 'timed_out':receipt.get('timed_out'),
                'cancelled':receipt.get('cancelled'), 'cancellation_reason':receipt.get('cancellation_reason'),
                'cancellation_observation':receipt.get('cancellation_observation')})
        return result
