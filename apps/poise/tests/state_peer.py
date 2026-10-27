"""Independent SQLite qualification and isolated historical-artifact checks."""
import argparse
import ast
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

# Isolated Python admits only this explicit test helper directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_sql import SQL_LOG, connect, read, seed, snapshot, verify_schema


def qualify():
    with closing(sqlite3.connect(':memory:', isolation_level=None)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE q_parent(id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE q_child(id TEXT PRIMARY KEY,parent TEXT REFERENCES q_parent(id))')
        db.execute('INSERT INTO q_parent(id) VALUES(?)', ('parent-A',))
        duplicate, foreign = False, False
        try:
            db.execute('INSERT INTO q_parent(id) VALUES(?)', ('parent-A',))
        except sqlite3.IntegrityError:
            duplicate = True
        try:
            db.execute('INSERT INTO q_child(id,parent) VALUES(?,?)', ('child-A', 'missing'))
        except sqlite3.IntegrityError:
            foreign = True
        db.execute('BEGIN')
        db.execute('CREATE TABLE q_partial(id TEXT)')
        db.execute('ROLLBACK')
        missing = db.execute('SELECT name FROM sqlite_master WHERE name=?', ('q_partial',)).fetchone() is None
        assert duplicate and foreign and missing
        return {'python': sys.version, 'sqlite': sqlite3.sqlite_version,
                'foreign_keys': db.execute('PRAGMA foreign_keys').fetchone()[0],
                'duplicate_refused': duplicate, 'foreign_refused': foreign,
                'ddl_rolled_back': missing}


def historical(args):
    migration = Path(args.migration)
    imports = []
    for node in ast.walk(ast.parse(migration.read_text())):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module
            imports.append(node.module)
    assert all(name.split('.')[0] in sys.stdlib_module_names for name in imports), imports
    spec = importlib.util.spec_from_file_location('historical_schema', migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = read(Path(args.fixtures) / 'schema.json')
    facts = read(Path(args.fixtures) / 'facts.json')
    root = Path(args.directory)
    with closing(connect(root / 'historical.sqlite')) as db:
        module.prepare(db)
        verify_schema(db, schema)
        assert snapshot(db, schema) == read(Path(args.fixtures) / 'empty-snapshot.json')
        seed(db, schema, facts)
        before = snapshot(db, schema)
        assert before == read(Path(args.fixtures) / 'snapshot.json')
        module.prepare(db)
        after = snapshot(db, schema)
        assert after == before
    attempts, trace = [], []
    with closing(connect(root / 'rollback.sqlite')) as db:
        def deny_later_table(action, name, unused, database, trigger):
            if action == sqlite3.SQLITE_CREATE_TABLE and name.startswith('poise_'):
                attempts.append(name)
                if len(attempts) == 2:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_trace_callback(trace.append)
        db.set_authorizer(deny_later_table)
        refused = False
        try:
            module.prepare(db)
        except sqlite3.DatabaseError:
            refused = True
        db.set_authorizer(None)
        tables = [row[0] for row in db.execute(
            'SELECT name FROM sqlite_master WHERE type=? AND name LIKE ?', ('table', 'poise_%'))]
        version = db.execute('PRAGMA user_version').fetchone()[0]
        assert refused and len(attempts) == 2, (refused, attempts)
        assert tables == [] and version == 0 and not db.in_transaction, (tables, version)
        module.prepare(db)
        verify_schema(db, schema)
        assert snapshot(db, schema) == read(Path(args.fixtures) / 'empty-snapshot.json')
    return {'isolated': True, 'imports': imports, 'before': before, 'after': after,
            'rollback': {'ddl_attempts': attempts, 'trace': trace,
                         'post_failure_tables': tables, 'post_failure_version': version,
                         'retry_version': 1}, 'sql': SQL_LOG}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['qualify', 'historical'])
    parser.add_argument('--migration')
    parser.add_argument('--fixtures')
    parser.add_argument('--directory')
    args = parser.parse_args()
    print(json.dumps(qualify() if args.mode == 'qualify' else historical(args),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
