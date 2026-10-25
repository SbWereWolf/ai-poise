"""SQLite record adapter; application operations choose the transaction boundary."""
from contextlib import contextmanager
import json

from apps.execution.domain.records import ExecutionRecord, RecordsError, canonical_json
from apps.execution.domain.outcomes import OUTCOME_RECORD_KIND


@contextmanager
def record_unit_of_work(lock, connect, write):
    with lock():
        connection = connect()
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield RecordRepository(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class RecordRepository:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def record(row):
        if row is None:
            return None
        return ExecutionRecord(row['kind'], row['record_id'], row['task_id'],
                               row['generation'], row['record_revision'], json.loads(row['data_json']))

    def get_request(self, request_id):
        return self.connection.execute('SELECT digest,response_json FROM requests WHERE request_id=?',
                                       (request_id,)).fetchone()

    def get_revision(self):
        return self.connection.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()[0]

    def save_request(self, request_id, digest, response):
        self.connection.execute('INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)',
                                (request_id, digest, canonical_json(response)))

    def set_revision(self, revision):
        self.connection.execute('UPDATE meta SET value=? WHERE key=?', (revision, 'revision'))

    def has_execution(self, task_id):
        return self.connection.execute('SELECT task_id FROM executions WHERE task_id=?', (task_id,)).fetchone() is not None

    def execution(self, task_id):
        return self.connection.execute(
            'SELECT task_id,generation,contract_revision,contract_json FROM executions WHERE task_id=?',
            (task_id,)).fetchone()

    def find_version(self, record):
        return self.record(self.connection.execute(
            'SELECT task_id,kind,record_id,record_revision,generation,data_json FROM execution_records '
            'WHERE task_id=? AND kind=? AND record_id=? AND record_revision=?',
            (record.task_id, record.kind, record.record_id, record.revision)).fetchone())

    def latest(self, task_id, kind, record_id):
        return self.record(self.connection.execute(
            'SELECT task_id,kind,record_id,record_revision,generation,data_json FROM execution_records '
            'WHERE task_id=? AND kind=? AND record_id=? ORDER BY record_revision DESC LIMIT 1',
            (task_id, kind, record_id)).fetchone())

    def insert(self, record):
        self.connection.execute(
            'INSERT INTO execution_records(task_id,kind,record_id,record_revision,generation,data_json) '
            'VALUES(?,?,?,?,?,?)',
            (record.task_id, record.kind, record.record_id, record.revision,
             record.generation, canonical_json(record.data)))

    def list_records(self, task_id):
        return [self.record(row) for row in self.connection.execute(
            'SELECT task_id,kind,record_id,record_revision,generation,data_json '
            'FROM execution_records WHERE task_id=? ORDER BY seq', (task_id,))]

    def outcome(self, outcome_id):
        return self.record(self.connection.execute(
            'SELECT task_id,kind,record_id,record_revision,generation,data_json '
            'FROM execution_records WHERE kind=? AND record_id=? ORDER BY seq DESC LIMIT 1',
            (OUTCOME_RECORD_KIND, outcome_id)).fetchone())


    def execution_state(self, task_id):
        row = self.connection.execute(
            'SELECT task_id,generation,contract_revision,contract_json,state,current_stage,iteration,'
            'workspace_id,admission_id FROM executions WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['contract'] = json.loads(value.pop('contract_json'))
        return value

    def save_execution_state(self, value, before):
        changed = self.connection.execute(
            'UPDATE executions SET generation=?,contract_revision=?,contract_json=?,state=?,'
            'current_stage=?,iteration=? WHERE task_id=? AND generation=? AND contract_revision=?',
            (value['generation'], value['contract_revision'], canonical_json(value['contract']),
             value['state'], value['current_stage'], value['iteration'], value['task_id'],
             before['generation'], before['contract_revision'])).rowcount
        if changed != 1:
            raise RecordsError('Lifecycle canonical generation or contract changed')
