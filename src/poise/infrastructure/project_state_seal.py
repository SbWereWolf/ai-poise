"""Complete admitted file seals and lossless SQLite comparison for relocation."""
import json

from ..common import PoiseError, file_digest
from .diagnostic_io import readonly_database


def _value(value):
    return {'blob': value.hex()} if isinstance(value, bytes) else value


def database_snapshot(path):
    with readonly_database(path) as db:
        schema = [dict(row) for row in db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        tables = {}
        for item in schema:
            if item['type'] == 'table':
                name = item['name']
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = [{key: _value(row[key]) for key in row.keys()}
                                for row in db.execute('SELECT * FROM ' + quoted)]
        return {'schema': schema, 'version': db.execute('PRAGMA user_version').fetchone()[0],
                'tables': tables}


def file_snapshot(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_file():
            item = {'digest': file_digest(path)}
            with path.open('rb') as stream:
                sqlite = stream.read(16) == b'SQLite format 3\x00'
            if sqlite:
                item['database'] = database_snapshot(path)
            result[path.relative_to(root).as_posix()] = item
    return result


def validate_source(root, snapshot):
    actual = {path.relative_to(root).as_posix(): file_digest(path)
              for path in root.rglob('*') if path.is_file()}
    if actual != {name: item['digest'] for name, item in snapshot.items()}:
        raise PoiseError('Admitted source files or history changed; source retirement refused')


def validate_database(before, after):
    if before['schema'] != after['schema'] or before['version'] != after['version']:
        raise PoiseError('Copied database schema changed after admission')
    for name, rows in before['tables'].items():
        actual = after['tables'][name]
        if name == 'sqlite_sequence':
            sequence = {row['name']: row['seq'] for row in actual}
            if any(sequence.get(row['name'], -1) < row['seq'] for row in rows):
                raise PoiseError('Copied database sequence lost admitted history')
        else:
            encode = lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False)
            # Preserve duplicate rows as well as values; additional committed
            # destination records are retained, never copied back over the source.
            from collections import Counter
            if Counter(map(encode, rows)) - Counter(map(encode, actual)):
                raise PoiseError('Copied database lost or changed admitted rows: ' + name)


def validate_destination(root, snapshot, normalize):
    for name, item in snapshot.items():
        path = root / name
        if not path.is_file():
            raise PoiseError('Copied working file is missing: ' + name)
        if file_digest(path) == item['digest']:
            continue
        if 'database' not in item:
            raise PoiseError('Copied working file changed: ' + name)
        actual = database_snapshot(path)
        normalize(path, item['database'], actual)
        validate_database(item['database'], actual)
