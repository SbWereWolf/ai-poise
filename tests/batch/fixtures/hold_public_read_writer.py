"""Independent test-only writer; never imports the application under test."""
import fcntl
from pathlib import Path
import sqlite3
import sys

kind, database, lock, test_root = sys.argv[1:]
root = Path(test_root).resolve()
assert Path(database).resolve().is_relative_to(root)
assert Path(lock).resolve().is_relative_to(root)
handle = None
connection = None
try:
    if kind == 'local-lock':
        handle = open(lock, 'a')
        fcntl.flock(handle, fcntl.LOCK_EX)
    elif kind in ('sqlite-writer', 'wal-message'):
        connection = sqlite3.connect(database, timeout=0, isolation_level=None)
        connection.execute('BEGIN IMMEDIATE')
        if kind == 'wal-message':
            connection.execute('INSERT INTO interaction_events(id,project,data,received_at) VALUES(?,?,?,?)',
                               ('fixture-wal-message', 'demo',
                                Path(__file__).with_name('public-read-wal-message.json').read_text(),
                                '2026-10-16T00:00:00+00:00'))
    else:
        raise AssertionError(kind)
    print('READY', flush=True)
    action = sys.stdin.readline().strip()
    assert action in ('RELEASE', 'COMMIT')
    if action == 'COMMIT':
        assert connection is not None
        connection.commit()
finally:
    if connection is not None:
        connection.rollback()
        connection.close()
    if handle is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
