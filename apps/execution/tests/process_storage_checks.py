"""Literal version1 stores and real SQLite interruption observations for T048."""
import json
import sqlite3

from process_checks import (APP, assert_same, fixture, installation, store,
                            current, write_json)
from store_checks import snapshot

CONNECT = sqlite3.connect
EXECUTIONS = ('task_id', 'generation', 'contract_revision', 'contract_json',
              'state', 'current_stage', 'iteration', 'workspace_id', 'admission_id')
RECORDS = ('seq', 'task_id', 'kind', 'record_id', 'record_revision', 'generation', 'data_json')
REQUESTS = ('request_id', 'digest', 'response_json')
EDITOR = ('request_id', 'request_digest', 'goal_type', 'source_revision', 'candidate', 'receipt', 'phase')


def legacy_values():
    return json.loads((APP / 'fixtures/store-records.json').read_text())


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def table_rows(connection, table, columns, order, schema='main'):
    return [list(row) for row in connection.execute(
        'SELECT ' + ','.join(quote(column) for column in columns)
        + ' FROM ' + quote(schema) + '.' + quote(table)
        + ' ORDER BY ' + ','.join(quote(column) for column in order))]


def retained_rows(path):
    with CONNECT(path) as db:
        return {'executions': table_rows(db, 'executions', EXECUTIONS, ['task_id']),
                'records': table_rows(db, 'execution_records', RECORDS, ['seq']),
                'requests': table_rows(db, 'requests', REQUESTS, ['request_id']),
                'revision': db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()[0]}


def install_populated_v1(root, mode='delete'):
    config = installation(root)
    core, editor = root / 'state/execution.sqlite', root / 'state/process-editor.sqlite'
    data = legacy_values()
    with CONNECT(core) as db:
        db.executescript((APP / 'fixtures/process-store-v1.sql').read_text())
        db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', 7))
        db.executemany('INSERT INTO executions(' + ','.join(EXECUTIONS) + ') VALUES(?,?,?,?,?,?,?,?,?)', data['source']['executions'])
        db.executemany('INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)', data['source']['requests'])
        for sequence, row in enumerate(data['initial_records'] + data['records'], 1):
            db.execute('INSERT INTO execution_records(' + ','.join(RECORDS) + ') VALUES(?,?,?,?,?,?,?)',
                       (sequence, row['task_id'], row['kind'], row['id'], row['revision'],
                        row['generation'], json.dumps(row['data'], ensure_ascii=False)))
    with CONNECT(editor) as db:
        db.executescript((APP / 'fixtures/process-editor-v1.sql').read_text())
    for path in [core, editor]:
        with CONNECT(path) as db:
            observed = db.execute('PRAGMA journal_mode=WAL' if mode == 'wal'
                                  else 'PRAGMA journal_mode=DELETE').fetchone()[0]
            assert_same(observed, mode)
    # Observe the old supported entry before invoking the missing capability.
    base = fixture('config')
    del base['process']
    del base['operator_extension']
    core_config = root / 'core.json'
    write_json(core_config, base)
    expected = [row for row in data['initial_records'] + data['records'] if row['task_id'] == '0042']
    assert_same(store(core_config).read_records('0042'), expected)
    with CONNECT(core) as db:
        assert_same(db.execute('PRAGMA user_version').fetchone()[0], 1)
    return config, core, editor


def modes(core, editor):
    result = []
    for path in [core, editor]:
        with CONNECT(path) as db:
            result.append(db.execute('PRAGMA journal_mode').fetchone()[0])
    return result


def assert_retained(core, before, expected_revision, expected_request_count, *, new_executions=0, new_records=0):
    actual = retained_rows(core)
    assert_same(len(actual['executions']), len(before['executions']) + new_executions)
    assert_same(len(actual['records']), len(before['records']) + new_records)
    assert_same(actual['executions'][:len(before['executions'])], before['executions'])
    assert_same(actual['records'][:len(before['records'])], before['records'])
    old_ids = {row[0] for row in before['requests']}
    assert_same([row for row in actual['requests'] if row[0] in old_ids], before['requests'])
    assert_same(actual['revision'], expected_revision)
    assert_same(len(actual['requests']), expected_request_count)


def editor_rows(editor):
    with CONNECT(editor) as db:
        return {'operations': table_rows(db, 'config_operations', EDITOR, ['request_id']),
                'heads': table_rows(db, 'config_heads', ['goal_type', 'revision'], ['goal_type'])}


def assert_v2_shape(core, old_schema):
    with CONNECT(core) as db:
        assert_same(db.execute('PRAGMA user_version').fetchone()[0], 2)
        assert_same([list(row) for row in db.execute('PRAGMA foreign_key_check')], [])
        foreign_keys = [list(row) for row in db.execute('PRAGMA foreign_key_list(execution_records)')]
        assert_same(foreign_keys, old_schema['foreign_keys'])
        columns = [list(row) for row in db.execute('PRAGMA table_info(executions)')]
        expected = [list(row) for row in old_schema['columns']]
        for row in expected:
            if row[1] == 'workspace_id':
                row[3] = 0
        assert_same(columns, expected)
        assert_same([list(row) for row in db.execute('PRAGMA table_info(execution_records)')],
                    old_schema['record_columns'])
        assert_same(record_indexes(db), old_schema['record_indexes'])


def record_indexes(db):
    result = []
    for row in db.execute('PRAGMA index_list(execution_records)'):
        columns = [list(value) for value in db.execute('PRAGMA index_info(' + quote(row[1]) + ')')]
        result.append([row[1], row[2], row[3], row[4], columns])
    return sorted(result, key=lambda row: row[0])


def schema_observation(core):
    with CONNECT(core) as db:
        return {'columns': [list(row) for row in db.execute('PRAGMA table_info(executions)')],
                'foreign_keys': [list(row) for row in db.execute('PRAGMA foreign_key_list(execution_records)')],
                'record_columns': [list(row) for row in db.execute('PRAGMA table_info(execution_records)')],
                'record_indexes': record_indexes(db)}


def migration_interrupt(checkpoint, observations):
    """Fail after real main-schema DDL or copied rows, without future private names."""
    class Interrupted(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.created = set()
            self.events = []
            self.set_authorizer(self.authorize)

        def authorize(self, action, table, column, database, cause):
            if database == 'main' and action in (sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_INSERT):
                self.events.append((action, table))
            return sqlite3.SQLITE_OK

        def execute(self, sql, parameters=()):
            self.events.clear()
            cursor = super().execute(sql, parameters)
            return self.after(cursor)

        def executemany(self, sql, parameters):
            self.events.clear()
            cursor = super().executemany(sql, parameters)
            return self.after(cursor)

        def after(self, cursor):
            events = list(self.events)
            for action, table in events:
                if action == sqlite3.SQLITE_CREATE_TABLE:
                    self.created.add(table)
                    if checkpoint == 'ddl':
                        observations.append({'checkpoint': 'ddl', 'table': table,
                                             'transaction_open': self.in_transaction})
                        raise sqlite3.OperationalError('test-owned migration interruption after DDL')
                elif checkpoint == 'data' and table in self.created:
                    count = super().execute('SELECT COUNT(*) FROM ' + quote(table)).fetchone()[0]
                    if count == 2:
                        observations.append({'checkpoint': 'data', 'table': table,
                                             'rows_copied': count, 'transaction_open': self.in_transaction})
                        raise sqlite3.OperationalError('test-owned migration interruption after copied rows')
            return cursor
    return Interrupted


def finalization_interrupt(config, observations):
    """Fail after an actual finalization write with published literal candidate."""
    expected = fixture('edited')
    columns = {'config_heads': ['goal_type', 'revision'],
               'requests': list(REQUESTS), 'meta': ['key', 'value']}
    core = config.parent / 'state/execution.sqlite'
    editor = config.parent / 'state/process-editor.sqlite'
    original = retained_rows(core)
    before = {'meta': [['revision', original['revision']]],
              'requests': original['requests'], 'config_heads': editor_rows(editor)['heads']}

    class Interrupted(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events = []
            self.set_authorizer(self.authorize)

        def authorize(self, action, table, column, database, cause):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE) and table in columns:
                self.events.append((database, table))
            return sqlite3.SQLITE_OK

        def execute(self, sql, parameters=()):
            self.events.clear()
            cursor = super().execute(sql, parameters)
            return self.after(cursor)

        def executemany(self, sql, parameters):
            self.events.clear()
            cursor = super().executemany(sql, parameters)
            return self.after(cursor)

        def after(self, cursor):
            events = list(self.events)
            target = current(config)
            if events and target.is_file() and json.loads(target.read_text()) == expected:
                database, table = events[-1]
                selected = ','.join(quote(column) for column in columns[table])
                rows = [list(row) for row in super().execute(
                    'SELECT ' + selected + ' FROM ' + quote(database) + '.' + quote(table)
                    + ' ORDER BY ' + quote(columns[table][0]))]
                try:
                    assert_same(rows, before[table])
                except AssertionError:
                    pass
                else:
                    return cursor
                observations.append({'table': table, 'database': database,
                                     'rows_after_successful_write': rows,
                                     'transaction_open': self.in_transaction})
                raise sqlite3.OperationalError('test-owned publication finalization interruption')
            return cursor
    return Interrupted


def connect_with(factory):
    def connect(*args, **kwargs):
        kwargs['factory'] = factory
        return CONNECT(*args, **kwargs)
    return connect


def assert_reopens_with_foreign_keys(config):
    owner = store(config)
    with owner.locked():
        connection = owner.connect()
        try:
            assert_same(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            assert_same([list(row) for row in connection.execute('PRAGMA foreign_key_check')], [])
        finally:
            connection.close()
