"""Bounded read-only observation primitives; no store initialization or migration."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat

from ..common import PoiseError
from .goal_config import strict_json


def regular_bytes(path, limit):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise PoiseError('Observation requires a regular file')
        value = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(value) > limit:
        raise PoiseError('Observation exceeds configured byte bound')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.lstat()):
        raise PoiseError('Observation changed while reading')
    return value


def json_file(path, limit):
    return strict_json(regular_bytes(path, limit).decode('utf-8'))


def json_record(value, limit):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
        raise PoiseError('Stored observation exceeds configured byte bound')
    return strict_json(value)


@contextmanager
def readonly_database(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise PoiseError('Observation database must be an existing regular file')
    # No immutable=1: the last committed WAL snapshot must remain visible.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                         timeout=0, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        if db.in_transaction:
            db.execute('ROLLBACK')
        db.close()
