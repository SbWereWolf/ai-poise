"""Process publication journal attached to the canonical Execution revision ledger."""
from contextlib import contextmanager
import json
from apps.execution.infrastructure.locking import resource_lock
from apps.execution.infrastructure.records_store import RecordRepository
from apps.execution.migrations.process_v1 import prepare


class ProcessRepository:
    def __init__(self, connection):
        self.connection = connection
        self.records = RecordRepository(connection)

    def revision(self):
        return int(self.records.get_revision())

    def completed(self, request):
        return self.records.get_request(request)

    def head(self, goal):
        row = self.connection.execute('SELECT revision FROM process_editor.config_heads WHERE goal_type=?', (goal,)).fetchone()
        return row[0] if row else None

    def pending(self, goal):
        row = self.connection.execute('SELECT request_id FROM process_editor.config_operations WHERE goal_type=? AND phase=? ORDER BY request_id', (goal,'pending')).fetchone()
        return row[0] if row else None

    def reserved(self, request):
        return self.connection.execute('''SELECT request_digest,candidate,receipt,phase
            FROM process_editor.config_operations WHERE request_id=?''', (request,)).fetchone()

    def reserve(self, request, digest, goal, previous, candidate, response):
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            self.connection.execute('''INSERT INTO process_editor.config_operations(
                request_id,request_digest,goal_type,source_revision,candidate,receipt,phase)
                VALUES(?,?,?,?,?,?,?)''', (request,digest,goal,previous,json.dumps(candidate,ensure_ascii=False),json.dumps(response,ensure_ascii=False),'pending'))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def finish(self, request, digest, goal, response):
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            self.connection.execute('''INSERT INTO process_editor.config_heads(goal_type,revision) VALUES(?,?)
                ON CONFLICT(goal_type) DO UPDATE SET revision=excluded.revision''', (goal,response['process_revision']))
            self.connection.execute('UPDATE process_editor.config_operations SET phase=? WHERE request_id=?', ('completed',request))
            self.records.set_revision(response['revision'])
            self.records.save_request(request,digest,response)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise


class ProcessUnitOfWork:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def open(self):
        config = self.store.config
        settings = config['process']
        with self.store.locked(), resource_lock(settings['lock'],config['lock_seconds'],config['poll_seconds']):
            connection = self.store.connect()
            try:
                settings['database'].parent.mkdir(parents=True, exist_ok=True)
                connection.execute('ATTACH DATABASE ? AS process_editor', (str(settings['database']),))
                prepare(connection)
                yield ProcessRepository(connection)
            finally:
                connection.close()
