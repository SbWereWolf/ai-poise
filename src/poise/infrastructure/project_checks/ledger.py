"""Project-owned functional run history and atomic diagnostic consumption."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from ...modules.project_checks.contracts import CheckFailure, require
from ...modules.project_checks.records import process_run, execution_item
from ...modules.project_checks.timing import eligible
from ..goal_config import atomic_write
from ..locking import exclusive_lock
from .inputs import canonical


class NamedRunLedger:
    def invocation_store(self, policy):
        from .invocations import FileCheckInvocationStore
        return FileCheckInvocationStore(self, policy)

    def __init__(self, configuration):
        self.configuration = configuration
        self.project = configuration.bound.config['project']
        storage = configuration.policy['storage']
        self.path = configuration.state / storage['ledger_file']
        self.lock_path = configuration.state / storage['lock_file']
        self.limits = configuration.bound.config['limits']
        self.file_mode = configuration.bound.config['batch']['file_mode']

    def invalid(self, exc):
        return CheckFailure('registry_invalid', 'policy.storage.ledger_file',
                            f'Реестр выбранного проекта непригоден: {exc}',
                            'Укажите реестр этого проекта либо восстановите его через владельца данных; исходный файл сохранён.')

    def _validate(self, connection):
        metadata = dict(connection.execute('SELECT key,value FROM metadata').fetchall())
        if metadata.get('project') != self.project or metadata.get('schema') != 'named-checks-ledger-1':
            raise self.invalid('project/schema mismatch')
        connection.execute('SELECT run_id,record_json FROM runs LIMIT 0')
        connection.execute('SELECT result_id,result_json FROM results LIMIT 0')
        connection.execute('SELECT evidence_run_id,run_id FROM diagnostic_uses LIMIT 0')

    @contextmanager
    def read(self):
        if not self.path.exists():
            yield None
            return
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                         timeout=self.limits['sqlite_read_seconds'])
            self._validate(connection)
            yield connection
        except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
            if isinstance(exc, CheckFailure):
                raise
            raise self.invalid(exc) from exc
        finally:
            if connection is not None:
                connection.close()

    def admit(self):
        with self.read():
            pass

    def decode(self, raw, table, identity):
        """Authoritative payload errors retain their row and field context."""
        field = 'run' if table == 'runs' else 'result'
        try:
            value = json.loads(raw)
            validate = process_run if table == 'runs' else execution_item
            validate(value, self.project, field)
            key = 'run_id' if table == 'runs' else 'result_id'
            require(value[key] == identity, field + '.' + key,
                    'Идентификатор payload не соответствует сохранённой записи.')
        except (ValueError, TypeError) as exc:
            raise self.invalid(f'{table}[{identity}]: {exc}') from exc
        return value

    def allocate(self, *, process):
        identity = uuid.uuid4().hex
        root = self.configuration.state / self.configuration.policy['storage']['runs_directory'] / identity
        return {'id': identity, 'record_path': str(root / ('record.json' if process else 'result.json')),
                'stdout': root / 'stdout.log', 'stderr': root / 'stderr.log',
                'progress': root / 'progress.jsonl', 'evidence': root / 'evidence'}

    @contextmanager
    def write(self):
        with exclusive_lock(self.lock_path, self.limits['lock_seconds'], self.limits['lock_poll_seconds']):
            # Check foreign/corrupt authoritative bytes before opening writable SQLite.
            exists = self.path.exists()
            if exists:
                self.admit()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = None
            try:
                connection = sqlite3.connect(self.path, timeout=self.limits['lock_seconds'])
                connection.execute('BEGIN IMMEDIATE')
                if not exists:
                    connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    connection.executemany('INSERT INTO metadata(key,value) VALUES(?,?)',
                                           [('schema', 'named-checks-ledger-1'), ('project', self.project)])
                    connection.execute('CREATE TABLE runs(run_id TEXT PRIMARY KEY,record_json TEXT NOT NULL)')
                    connection.execute('CREATE TABLE results(result_id TEXT PRIMARY KEY,result_json TEXT NOT NULL)')
                    connection.execute('CREATE TABLE diagnostic_uses(evidence_run_id TEXT PRIMARY KEY,run_id TEXT NOT NULL)')
                else:
                    self._validate(connection)
                yield connection
                connection.commit()
                self.path.chmod(self.file_mode)
            except Exception:
                if connection is not None:
                    connection.rollback()
                raise
            finally:
                if connection is not None:
                    connection.close()

    def run(self, run_id):
        with self.read() as connection:
            row = None if connection is None else connection.execute(
                'SELECT record_json FROM runs WHERE run_id=?', (run_id,)).fetchone()
            return None if row is None else self.decode(row[0], 'runs', run_id)

    def result(self, result_id, field):
        with self.read() as connection:
            row = None if connection is None else connection.execute(
                'SELECT result_json FROM results WHERE result_id=?', (result_id,)).fetchone()
            if row is None:
                raise CheckFailure('result_missing', field,
                                   f'Сохранённый результат {result_id} не найден в выбранном проекте.',
                                   'Передайте result_id существующего сохранённого результата выбранного проекта.',
                                   reason=f'Запрошенный result_id {result_id} отсутствует.')
            return self.decode(row[0], 'results', result_id)

    def samples(self, expected_identity):
        with self.read() as connection:
            rows = [] if connection is None else connection.execute(
                'SELECT run_id,record_json FROM runs ORDER BY rowid').fetchall()
            records = [self.decode(raw, 'runs', identity) for identity, raw in rows]
            return [record for record in records if eligible(record, expected_identity)]

    def consumed(self, evidence_run_id):
        with self.read() as connection:
            return connection is not None and connection.execute(
                'SELECT run_id FROM diagnostic_uses WHERE evidence_run_id=?', (evidence_run_id,)).fetchone() is not None

    def reserve_diagnostic(self, evidence_run_id, run_id):
        try:
            with self.write() as connection:
                connection.execute('INSERT INTO diagnostic_uses(evidence_run_id,run_id) VALUES(?,?)',
                                   (evidence_run_id, run_id))
        except sqlite3.IntegrityError as exc:
            raise CheckFailure('override_consumed', 'timeout_override.evidence_run_id',
                               'Основание уже использовано.', 'Получите новое исходное прерывание.') from exc

    def release_unstarted_diagnostic(self, evidence_run_id, run_id):
        with self.write() as connection:
            connection.execute('DELETE FROM diagnostic_uses WHERE evidence_run_id=? AND run_id=?',
                               (evidence_run_id, run_id))

    def save_run(self, record):
        atomic_write(Path(record['record_path']), canonical(record) + b'\n', self.file_mode)
        with self.write() as connection:
            connection.execute('INSERT INTO runs(run_id,record_json) VALUES(?,?)',
                               (record['run_id'], canonical(record).decode()))

    def save_result(self, result):
        atomic_write(Path(result['record_path']), canonical(result) + b'\n', self.file_mode)
        with self.write() as connection:
            connection.execute('INSERT INTO results(result_id,result_json) VALUES(?,?)',
                               (result['result_id'], canonical(result).decode()))
