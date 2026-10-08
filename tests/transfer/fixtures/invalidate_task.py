"""Invalidate the disposable staged Task row before the real final validator."""
from pathlib import Path
import sqlite3
import sys
snapshot = Path(sys.argv[1])
assert snapshot.is_file(), 'The real pipeline must have extracted its snapshot'
with sqlite3.connect(snapshot) as database:
    assert database.execute('SELECT id FROM tasks').fetchall() == [('T1',)]
    database.execute('UPDATE tasks SET metadata=? WHERE id=?', ('{}', 'T1'))
