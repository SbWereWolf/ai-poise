"""Session-owned two-table store; no Task schema initialization or migration."""
from contextlib import contextmanager

from ...common import PoiseError
from .transaction import write_transaction


class SessionDatabase:
    def __init__(self, path, lock, wait, poll, read_wait, project_fence):
        self.path, self.lock, self.wait, self.poll = path, lock, wait, poll
        self.read_wait, self.project_fence = read_wait, project_fence
        with self.write_transaction() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {'runtime_bindings', 'runtime_cursors'}
            if version == 0 and not tables:
                db.execute('CREATE TABLE runtime_bindings(identity_key TEXT PRIMARY KEY, '
                           'session_id TEXT NOT NULL UNIQUE, inventory TEXT NOT NULL)')
                db.execute('CREATE TABLE runtime_cursors(session_id TEXT NOT NULL, '
                           'path TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(session_id,path))')
                db.execute('PRAGMA user_version=1')
            elif version != 1 or tables != expected:
                raise PoiseError('runtime_store_incompatible: select an empty session store '
                                 'or connect its existing Task adapter; no migration performed')

    @contextmanager
    def write_transaction(self):
        with self.project_fence.locked():
            with write_transaction(self.path, self.lock, self.wait, self.poll) as db:
                yield db

    @contextmanager
    def read_transaction(self):
        from ..diagnostic_io import readonly_transaction
        with self.project_fence.locked():
            with readonly_transaction(self.path, self.read_wait, self.poll) as db:
                yield db
