"""Replay using only installed yoyo and files, never application imports."""
import importlib.util
from pathlib import Path
import sqlite3
import sys

def history_backend(target, kind):
    from yoyo import get_backend
    table = '_fixture_' + kind
    backend = get_backend('sqlite:///' + str(target), migration_table=table)
    backend.log_table = table + '_log'
    backend.version_table = table + '_version'
    backend.connection.execute('PRAGMA foreign_keys=ON')
    return backend


def main(arguments):
    sys.path.insert(0, arguments[0])
    assert importlib.util.find_spec('poise') is None
    from yoyo import read_migrations
    target = Path(arguments[3])
    schema = history_backend(target, 'schema')
    schema.apply_migrations(schema.to_apply(read_migrations(arguments[1])))
    data = history_backend(target, 'data')
    migrations = read_migrations(arguments[2])
    data.apply_migrations(data.to_apply(migrations))
    with sqlite3.connect(target) as db:
        first = db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall()
    data.rollback_migrations(data.to_rollback(migrations))
    data.apply_migrations(data.to_apply(migrations))
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall() == first


if __name__ == '__main__':
    main(sys.argv[1:])
