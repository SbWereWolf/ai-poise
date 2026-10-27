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
    db.set_trace_callback(lambda statement: SQL_LOG.append({'trace': statement}))
    db.execute('PRAGMA foreign_keys=ON')
    return db


def names(values):
    return ','.join('"' + value + '"' for value in values)


def snapshot(db, schema):
    result = {'version': db.execute('PRAGMA user_version').fetchone()[0], 'rows': {}}
    SQL_LOG.append({'pragma_user_version': result['version']})
    for table, spec in schema.items():
        query = f'SELECT {names(spec["columns"])} FROM "{table}" ORDER BY {names(spec["order_by"])}'
        entry = {'sql': query, 'parameters': []}
        SQL_LOG.append(entry)
        try:
            rows = [list(row) for row in db.execute(query)]
        except sqlite3.Error as exc:
            entry['error'] = str(exc)
            raise
        entry['rows'] = rows
        result['rows'][table] = rows
    return result


def seed(db, schema, facts):
    db.execute('BEGIN')
    try:
        for table, spec in schema.items():
            query = f'INSERT INTO "{table}"({names(spec["columns"])}) VALUES({",".join("?" for _ in spec["columns"])})'
            for row in facts[table]:
                entry = {'sql': query, 'parameters': row}
                SQL_LOG.append(entry)
                try:
                    entry['rowcount'] = db.execute(query, row).rowcount
                except sqlite3.Error as exc:
                    entry['error'] = str(exc)
                    raise
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def verify_schema(db, schema):
    tables = {row[0] for row in db.execute(
        'SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ?', ('table', 'sqlite_%'))}
    SQL_LOG.append({'actual_tables': sorted(tables)})
    assert set(schema) <= tables, tables
    for table, spec in schema.items():
        columns = list(db.execute(f'PRAGMA table_info("{table}")'))
        foreign = list(db.execute(f'PRAGMA foreign_key_list("{table}")'))
        unique = []
        for index in db.execute(f'PRAGMA index_list("{table}")').fetchall():
            if index[2] and not index[4]:
                quoted = index[1].replace('"', '""')
                unique.append([row[2] for row in db.execute(f'PRAGMA index_info("{quoted}")')])
        SQL_LOG.append({'table': table, 'columns': [list(row) for row in columns],
                        'foreign_keys': [list(row) for row in foreign], 'unique': unique})
        observed = {row[1]: row[2].upper() for row in columns}
        expected = dict(zip(spec['columns'], spec['types']))
        assert all(observed.get(name) == value for name, value in expected.items()), (table, observed)
        by_name = {row[1]: row for row in columns}
        assert all(by_name[name][3] == 1 for name in spec['not_null']), (table, columns)
        actual_pk = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        assert actual_pk == spec['primary_key'], (table, actual_pk)
        actual_foreign = {(row[3], row[2], row[4]) for row in foreign}
        assert set(map(tuple, spec['foreign_keys'])) <= actual_foreign, (table, foreign)
        assert set(map(tuple, spec['unique'])) <= set(map(tuple, unique)), (table, unique)
