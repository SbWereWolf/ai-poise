"""Exact file seals; only identified owners may normalize database effects."""
from collections import Counter
import json

from ..common import PoiseError, file_digest
from .diagnostic_io import readonly_database


def _value(value):
    return {'blob': value.hex()} if isinstance(value, bytes) else value


def database_snapshot(path):
    with readonly_database(path) as db:
        schema = [dict(row) for row in db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        tables, identities = {}, {}
        rowid_tables = {row['name'] for row in db.execute('PRAGMA table_list')
                        if row['schema'] == 'main' and not row['wr']}
        for item in schema:
            if item['type'] == 'table':
                name = item['name']
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [row['name'] for row in db.execute('PRAGMA table_xinfo(' + quoted + ')')]
                selection = ['"' + column.replace('"', '""') + '"' for column in columns]
                alias = None
                if name in rowid_tables:
                    identity = next((key for key in ('_rowid_', 'rowid', 'oid') if key not in columns), None)
                    if identity is None:
                        raise PoiseError('Owned database row identity cannot be observed: ' + name)
                    alias = '__relocation_rowid__'
                    while alias in columns:
                        alias += '_'
                    selection.insert(0, identity + ' AS "' + alias + '"')
                identities[name] = alias
                tables[name] = [{key: _value(row[key]) for key in row.keys()}
                                for row in db.execute('SELECT ' + ','.join(selection) + ' FROM ' + quoted)]
        return {'schema': schema, 'version': db.execute('PRAGMA user_version').fetchone()[0],
                'application_id': db.execute('PRAGMA application_id').fetchone()[0],
                'row_identity_fields': identities, 'tables': tables}


def file_snapshot(root, databases):
    result = {}
    for path in root.rglob('*'):
        if path.is_file():
            item = {'digest': file_digest(path)}
            if path in databases:
                item['database'] = database_snapshot(path)
            result[path.relative_to(root).as_posix()] = item
    return result


def validate_source(root, snapshot):
    actual = {path.relative_to(root).as_posix(): file_digest(path)
              for path in root.rglob('*') if path.is_file()}
    if actual != {name: item['digest'] for name, item in snapshot.items()}:
        raise PoiseError('Admitted source files or history changed; source retirement refused')


def validate_database(before, after):
    if any(before[key] != after[key] for key in (
            'schema', 'version', 'application_id', 'row_identity_fields')):
        raise PoiseError('Copied database schema or identity changed after admission')
    for name, rows in before['tables'].items():
        actual = after['tables'][name]
        encode = lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False)
        if Counter(map(encode, rows)) != Counter(map(encode, actual)):
            raise PoiseError('Copied database has unconfirmed row changes: ' + name)


def validate_destination(root, snapshot, databases, normalize):
    for name, item in snapshot.items():
        path = root / name
        if not path.is_file():
            raise PoiseError('Copied working file is missing: ' + name)
        if file_digest(path) == item['digest']:
            continue
        if path not in databases or 'database' not in item:
            raise PoiseError('Copied working file changed: ' + name)
        actual = database_snapshot(path)
        normalize(path, item['database'], actual)
        validate_database(item['database'], actual)
