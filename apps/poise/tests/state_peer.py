"""Independent SQLite qualification and isolated historical-artifact checks."""
import argparse
import ast
from contextlib import closing, contextmanager
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
        db.set_trace_callback(lambda statement: SQL_LOG.append({'trace': statement}))
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


@contextmanager
def observed_database(path, section):
    """Save actual connection state before close, including during an exception."""
    with closing(connect(path)) as db:
        db.set_trace_callback(section.setdefault('trace', []).append)
        try:
            yield db
        finally:
            original = sys.exception()
            if original is not None:
                section['raised'] = {'type': type(original).__name__, 'message': str(original)}
            try:
                section['observation'] = {
                    'version': db.execute('PRAGMA user_version').fetchone()[0],
                    'tables': [list(row) for row in db.execute(
                        'SELECT name,sql FROM sqlite_master WHERE type=? ORDER BY name', ('table',))],
                    'in_transaction': db.in_transaction}
            except Exception as exc:
                section['observation_error'] = {'type': type(exc).__name__, 'message': str(exc)}
                if original is None:
                    raise


def diagnostic(report):
    """An intentional test-peer failure qualifies the same finally/output path."""
    section = report.setdefault('diagnostic', {})
    with observed_database(':memory:', section) as db:
        db.execute('BEGIN')
        db.execute('CREATE TABLE q_diagnostic(value TEXT NOT NULL)')
        db.execute('INSERT INTO q_diagnostic(value) VALUES(?)', ('before-failure',))
        section['rows'] = [list(row) for row in db.execute('SELECT value FROM q_diagnostic')]
        raise AssertionError('intentional diagnostic failure')


def historical(args, report):
    migration = Path(args.migration)
    imports = report.setdefault('imports', [])
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
    report['isolated'] = True
    with observed_database(root / 'historical.sqlite', report.setdefault('historical', {})) as db:
        module.prepare(db)
        verify_schema(db, schema)
        assert snapshot(db, schema) == read(Path(args.fixtures) / 'empty-snapshot.json')
        seed(db, schema, facts)
        report['before'] = snapshot(db, schema)
        assert report['before'] == read(Path(args.fixtures) / 'snapshot.json')
        module.prepare(db)
        report['after'] = snapshot(db, schema)
        assert report['after'] == report['before']
    attempts, trace = [], []
    rollback = report.setdefault('rollback', {'ddl_attempts': attempts, 'trace': trace})
    with observed_database(root / 'rollback.sqlite', rollback) as db:
        def deny_later_table(action, name, unused, database, trigger):
            if action == sqlite3.SQLITE_CREATE_TABLE and name.startswith('poise_'):
                attempts.append(name)
                if len(attempts) == 2:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(deny_later_table)
        refused = False
        try:
            module.prepare(db)
        except sqlite3.DatabaseError as exc:
            refused = True
            rollback['expected_sqlite_failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        finally:
            db.set_authorizer(None)
        tables = [row[0] for row in db.execute(
            'SELECT name FROM sqlite_master WHERE type=? AND name LIKE ?', ('table', 'poise_%'))]
        version = db.execute('PRAGMA user_version').fetchone()[0]
        rollback.update(post_failure_tables=tables, post_failure_version=version,
                        in_transaction_before_cleanup=db.in_transaction, refused=refused)
        assert refused and len(attempts) == 2, (refused, attempts)
        assert tables == [] and version == 0 and not db.in_transaction, (tables, version)
        module.prepare(db)
        verify_schema(db, schema)
        assert snapshot(db, schema) == read(Path(args.fixtures) / 'empty-snapshot.json')
        rollback['retry_version'] = db.execute('PRAGMA user_version').fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['qualify', 'historical', 'diagnostic'])
    parser.add_argument('--migration')
    parser.add_argument('--fixtures')
    parser.add_argument('--directory')
    args = parser.parse_args()
    report = {'sql': SQL_LOG}
    code = 0
    try:
        if args.mode == 'qualify':
            report.update(qualify())
        elif args.mode == 'diagnostic':
            diagnostic(report)
        else:
            historical(args, report)
        report['status'] = 'passed'
    except AssertionError as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
        code = 1
    except Exception as exc:
        report.update(status='error', error={'type': type(exc).__name__, 'message': str(exc)})
        code = 2
    finally:
        print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
