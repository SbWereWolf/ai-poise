"""Accounting-only optional storage; existing Task stores retain their owner."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import math

from ...common import PoiseError
from .transaction import write_transaction


APPLICATION_ID = 0x50414D31
FORMAT_VERSION = 1
TABLE_COLUMNS = {
    'accounting_accounts': ('task_id', 'project', 'created_at', 'data'),
    'accounting_usage': ('id', 'task_id', 'project', 'source', 'stream', 'sequence', 'occurred_at', 'data'),
    'accounting_cycles': ('id', 'task_id', 'project', 'session_id', 'started_at', 'ended_at', 'data'),
    'accounting_credits': ('seq', 'id', 'task_id', 'project', 'at', 'data'),
    'accounting_findings': ('task_id', 'finding_id', 'data'),
}
SCHEMA = (
    'CREATE TABLE accounting_accounts(task_id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)',
    'CREATE TABLE accounting_usage(id TEXT PRIMARY KEY, task_id TEXT, project TEXT NOT NULL, source TEXT NOT NULL, stream TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(source,stream,sequence))',
    'CREATE INDEX accounting_usage_task ON accounting_usage(task_id,occurred_at)',
    'CREATE TABLE accounting_cycles(id TEXT PRIMARY KEY, task_id TEXT, project TEXT NOT NULL, session_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, data TEXT NOT NULL)',
    'CREATE UNIQUE INDEX accounting_open_session ON accounting_cycles(session_id) WHERE ended_at IS NULL',
    'CREATE INDEX accounting_cycle_task ON accounting_cycles(task_id,started_at)',
    'CREATE TABLE accounting_credits(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL, project TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)',
    'CREATE INDEX accounting_credit_task ON accounting_credits(task_id,seq)',
    'CREATE TABLE accounting_findings(task_id TEXT NOT NULL, finding_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,finding_id))',
)


def validate_accounting_schema(db):
    """Recognize existing formats using schema metadata, without Task reads."""
    application = db.execute('PRAGMA application_id').fetchone()[0]
    version = db.execute('PRAGMA user_version').fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    current = application == APPLICATION_ID and version == FORMAT_VERSION
    legacy = application == 0 and version == 13 and {'tasks', 'task_workflows', 'sessions'} <= tables
    if not (current or legacy) or not TABLE_COLUMNS.keys() <= tables:
        raise PoiseError('Unrecognized optional accounting store; select the known accounting database or a new empty path')
    if current and tables - (TABLE_COLUMNS.keys() | {'sqlite_sequence'}):
        raise PoiseError('Foreign tables in accounting-only store; preserve this database and select its actual owner')
    for name, columns in TABLE_COLUMNS.items():
        actual = tuple(r[1] for r in db.execute('PRAGMA table_info(' + name + ')'))
        if actual != columns:
            raise PoiseError('Unsupported accounting table layout: ' + name + '; preserve existing data and select its supported owner')


class AccountingDatabase:
    """Initialize only the empty optional store through shared writer mechanics."""
    accounting_only = True

    def __init__(self, path, lock, wait, poll, project_fence=None):
        for value in (wait, poll):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise PoiseError('Explicit finite positive accounting lock bounds required')
        self.path, self.lock = Path(path), Path(lock)
        self.wait, self.poll, self.project_fence = wait, poll, project_fence
        with self.transaction() as db:
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not tables and db.execute('PRAGMA user_version').fetchone()[0] == 0 and db.execute('PRAGMA application_id').fetchone()[0] == 0:
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                db.execute(f'PRAGMA user_version={FORMAT_VERSION}')
            validate_accounting_schema(db)

    @contextmanager
    def transaction(self):
        with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
            with write_transaction(self.path, self.lock, self.wait, self.poll) as db:
                yield db
