"""Test-owned SQLite arrangement/oracles for the Execution persistence boundary."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

APP = Path(__file__).resolve().parents[1]
CONNECT = sqlite3.connect
FIELDS = ('task_id', 'kind', 'record_id', 'record_revision', 'generation', 'data_json')


def fixture():
    return json.loads((APP / 'fixtures/store-records.json').read_text())


def assert_same(actual, expected, path='$'):
    """Compare every JSON type and value, preserving array order independently."""
    assert type(actual) is type(expected), f'{path}: {type(actual).__name__} != {type(expected).__name__}'
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), f'{path}: different object fields'
        for key in expected:
            assert_same(actual[key], expected[key], path + '.' + key)
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f'{path}: different array lengths'
        for index, (value, oracle) in enumerate(zip(actual, expected)):
            assert_same(value, oracle, f'{path}[{index}]')
    else:
        assert actual == expected, f'{path}: {actual!r} != {expected!r}'


def subject():
    spec = importlib.util.spec_from_file_location('execution_store_subject', APP / 'shell.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def installation(root, *, source=False):
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads((APP / 'fixtures/execution-config.json').read_text())
    config['paths'] = {'state': 'state', 'database': 'execution.sqlite', 'lock': 'execution.lock'}
    path = root / 'execution.json'
    path.write_text(json.dumps(config))
    database = root / 'state/execution.sqlite'
    database.parent.mkdir()
    if source:
        values = fixture()['source']
        with CONNECT(database) as db:
            db.executescript((APP / 'fixtures/store-source.sql').read_text())
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', values['revision']))
            db.executemany('INSERT INTO executions(task_id,generation,contract_revision,contract_json,state,current_stage,iteration,workspace_id,admission_id) VALUES(?,?,?,?,?,?,?,?,?)', values['executions'])
            db.executemany('INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)', values['requests'])
    module = subject()
    return module, module.Store(module.load_config(path)), database, path


def prepare(store):
    with store.locked():
        connection = store.connect()
        connection.close()


def snapshot(path):
    with CONNECT(path) as db:
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables = {}
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            # Identifiers come solely from sqlite_master, quoted as identifiers.
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in db.execute('PRAGMA table_info(' + quoted + ')')]
            selection = ','.join('"' + column.replace('"', '""') + '"' for column in columns)
            rows = db.execute('SELECT ' + selection + ' FROM ' + quoted).fetchall()
            tables[name] = sorted(rows, key=repr)
        return {'schema': schema, 'tables': tables, 'version': db.execute('PRAGMA user_version').fetchone()[0]}


def original_rows(path):
    with CONNECT(path) as db:
        return {
            'executions': db.execute('SELECT task_id,generation,contract_revision,contract_json,state,current_stage,iteration,workspace_id,admission_id FROM executions ORDER BY task_id').fetchall(),
            'requests': db.execute('SELECT request_id,digest,response_json FROM requests ORDER BY request_id').fetchall(),
            'revision': db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0],
        }


def stored_records(path, task_id=None):
    with CONNECT(path) as db:
        sql = 'SELECT ' + ','.join(FIELDS) + ' FROM execution_records'
        rows = db.execute(sql + (' WHERE task_id=?' if task_id else '') + ' ORDER BY seq', (task_id,) if task_id else ()).fetchall()
    return [{'task_id': task, 'kind': kind, 'id': rid, 'revision': revision,
             'generation': generation, 'data': json.loads(data)}
            for task, kind, rid, revision, generation, data in rows]


def initial_records():
    return fixture()['initial_records']


def write(store, records=None, *, request='records-1', revision=7):
    return store.write_records(request_id=request, expected_revision=revision,
                               records=deepcopy(fixture()['records'] if records is None else records))


def revision(path):
    with CONNECT(path) as db:
        return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]


class ConnectionProxy:
    """Observe actual table reads through SQLite's parser, not SQL spelling."""
    def __init__(self, inner, fetched):
        self.inner, self.on_fetched = inner, fetched
        self.read_count = 0
        self.statements = {}
        inner.set_authorizer(self.authorize)

    def authorize(self, action, table, column, database, source):
        if action == sqlite3.SQLITE_READ and table == 'execution_records' and database == 'main':
            self.read_count += 1
        return sqlite3.SQLITE_OK

    def execute_on(self, cursor, sql, parameters):
        before = self.read_count
        cursor.execute(sql, parameters)
        observed = self.read_count != before or self.statements.get(sql, False)
        self.statements[sql] = observed
        return CursorProxy(cursor, self.on_fetched, observed, self)

    def __getattr__(self, key):
        return getattr(self.inner, key)

    @property
    def row_factory(self):
        return self.inner.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.inner.row_factory = value

    def execute(self, sql, parameters=()):
        return self.execute_on(self.inner.cursor(), sql, parameters)

    def cursor(self, *args, **kwargs):
        return CursorProxy(self.inner.cursor(*args, **kwargs), self.on_fetched, False, self)

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)


class CursorProxy:
    def __init__(self, inner, fetched, observed, connection):
        self.inner, self.fetched = inner, fetched
        self.observed, self.owner = observed, connection

    def execute(self, sql, parameters=()):
        result = self.owner.execute_on(self.inner, sql, parameters)
        self.observed = result.observed
        return self

    def notify(self):
        if self.observed:
            self.fetched()

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def fetchall(self):
        rows = self.inner.fetchall()
        self.notify()
        return rows

    def fetchone(self):
        row = self.inner.fetchone()
        self.notify()
        return row

    def fetchmany(self, *args):
        rows = self.inner.fetchmany(*args)
        self.notify()
        return rows

    def __iter__(self):
        rows = self.inner.fetchall()
        self.notify()
        return iter(rows)
