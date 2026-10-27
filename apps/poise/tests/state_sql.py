"""Independent SQL arrangement/oracles from test-owned literal fixtures."""
import json
from pathlib import Path
import sqlite3


SQL_LOG = []


def read(path):
    return json.loads(Path(path).read_text())


def connect(path, *, readonly=False):
    location = Path(path).absolute().as_uri() + '?mode=ro' if readonly else str(path)
    db = sqlite3.connect(location, uri=readonly, timeout=0, isolation_level=None)
    db.execute('PRAGMA foreign_keys=ON')
    return db


def names(values):
    return ','.join('"' + value + '"' for value in values)


def snapshot(db, schema):
    result = {'version': db.execute('PRAGMA user_version').fetchone()[0], 'rows': {}}
    for table, spec in schema.items():
        query = f'SELECT {names(spec["columns"])} FROM "{table}" ORDER BY {names(spec["order_by"])}'
        rows = [list(row) for row in db.execute(query)]
        result['rows'][table] = rows
        SQL_LOG.append({'sql': query, 'parameters': [], 'rows': rows})
    return result


def seed(db, schema, facts):
    db.execute('BEGIN')
    try:
        for table, spec in schema.items():
            query = f'INSERT INTO "{table}"({names(spec["columns"])}) VALUES({",".join("?" for _ in spec["columns"])})'
            for row in facts[table]:
                db.execute(query, row)
                SQL_LOG.append({'sql': query, 'parameters': row})
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def verify_schema(db, schema):
    tables = {row[0] for row in db.execute(
        'SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ?', ('table', 'sqlite_%'))}
    assert set(schema) <= tables, tables
    for table, spec in schema.items():
        columns = list(db.execute(f'PRAGMA table_info("{table}")'))
        observed = {row[1]: row[2].upper() for row in columns}
        expected = dict(zip(spec['columns'], spec['types']))
        assert all(observed.get(name) == value for name, value in expected.items()), (table, observed)
        SQL_LOG.append({'table': table, 'columns': [list(row) for row in columns],
                        'foreign_keys': [list(row) for row in db.execute(f'PRAGMA foreign_key_list("{table}")')]})
