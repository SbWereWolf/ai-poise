"""One bounded SQLite write boundary; a transaction body is never replayed."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from time import monotonic, sleep

from ...modules.foundation.errors import PoiseError
from ..locking import exclusive_lock


def _boundary(db: sqlite3.Connection, sql: str, path: Path, wait: float, poll: float):
    deadline = monotonic() + wait
    while True:
        try:
            db.execute(sql)
            return
        except sqlite3.OperationalError as exc:
            # A normal external writer/reader lock is retryable at these two
            # boundaries. SQLITE_LOCKED and BUSY_SNAPSHOT need a different action;
            # never match exception prose or replay an operation to hide them.
            if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_BUSY:
                raise
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PoiseError(
                    f'SQLite {sql}: истёк lock_seconds={wait:g} для {path}. '
                    'Закройте удерживающую транзакцию в другом процессе; '
                    'не удаляйте БД или lock-файлы. Повторите операцию после освобождения.'
                ) from exc
            sleep(min(poll, remaining))


@contextmanager
def write_transaction(path: Path, lock: Path, wait: float, poll: float):
    """Serialize owners, acquire before reads and retry COMMIT without body replay.

    The existing explicit wait/poll settings bound each lock acquisition/COMMIT,
    not application execution. An exhausted COMMIT rolls back the whole write.
    """
    with exclusive_lock(lock, wait, poll):
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=0, isolation_level=None, autocommit=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            if db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
                raise PoiseError('SQLite foreign_keys не включён')
            _boundary(db, 'BEGIN IMMEDIATE', path, wait, poll)
            yield db
            _boundary(db, 'COMMIT', path, wait, poll)
        except BaseException as exc:
            if db.in_transaction:
                try:
                    db.execute('ROLLBACK')
                except sqlite3.Error as cleanup:
                    exc.add_note(f'SQLite rollback failed for {path}: {cleanup}')
            raise
        finally:
            db.close()
