"""Replay using only installed yoyo and files, never application imports."""
import importlib.util
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec('poise') is None
from yoyo import get_backend, read_migrations

target = Path(sys.argv[4])
schema = get_backend('sqlite:///' + str(target), migration_table='_fixture_schema')
schema.connection.execute('PRAGMA foreign_keys=ON')
schema.apply_migrations(schema.to_apply(read_migrations(sys.argv[2])))
data = get_backend('sqlite:///' + str(target), migration_table='_fixture_data')
data.connection.execute('PRAGMA foreign_keys=ON')
migrations = read_migrations(sys.argv[3])
data.apply_migrations(data.to_apply(migrations))
with sqlite3.connect(target) as db:
    first = db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall()
data.rollback_migrations(data.to_rollback(migrations))
data.apply_migrations(data.to_apply(migrations))
with sqlite3.connect(target) as db:
    assert db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall() == first
