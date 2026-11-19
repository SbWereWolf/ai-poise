"""Literal fixtures, real CLI and independent standard-yoyo setup only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys

from conftest import write_json
from requirements_registry.migration_support import (
    COLUMNS, create_database, migration, snapshot, sql,
)

SOURCE = Path(os.environ['POISE_TEST_SOURCE_ROOT']).resolve()
FIXTURES = Path(__file__).parent / 'fixtures/task_independent_writes'
DATA = json.loads((FIXTURES / 'literals.json').read_text())


def environment(binding):
    value = {k: v for k, v in os.environ.items() if not (
        k.startswith(('CODEX_', 'POISE_')) or k in {'PYTHONPATH', 'PYTHONHOME'}
    )}
    value['PYTHONPATH'] = os.pathsep.join([
        str(SOURCE / '.runtime/dependencies'), str(SOURCE / 'src'),
    ])
    value['POISE_CALLER_BINDING'] = str(binding)
    value['PYTHONDONTWRITEBYTECODE'] = '1'
    return value


def history_backend(database, kind='data'):
    from yoyo import get_backend
    name = '_poise_requirements_' + kind
    backend = get_backend('sqlite:///' + str(database), migration_table=name)
    backend.log_table = name + '_log'
    backend.version_table = name + '_version'
    backend.connection.execute('PRAGMA foreign_keys=ON')
    return backend


def history_ids(database):
    return sql(database, 'SELECT migration_id FROM _poise_requirements_data ORDER BY migration_id')


def file_state(root):
    excluded = {'.git', '.runtime', '__pycache__', '.pytest_cache'}
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and not set(p.relative_to(root).parts) & excluded}


def task_state(project):
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    if not database.exists():
        return None
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        rows = db.execute(DATA['task_rows_query']).fetchall()
        journal = db.execute(DATA['task_journal_query']).fetchall()
    return rows, journal


class Application:
    def __init__(self, project, *, empty=True):
        self.project = project
        self.config = project['config_path']
        self.cfg = json.loads(self.config.read_text())
        self.database = project['root'] / self.cfg['paths']['state'] / self.cfg['paths']['requirements_database']
        self.task_database = project['root'] / self.cfg['paths']['state'] / self.cfg['paths']['database']
        self.history = Path(self.cfg['paths']['requirements_data_migrations'])
        self.schema = Path(self.cfg['paths']['requirements_schema_migrations'])
        self.bootstrap = Path(self.cfg['paths']['requirements_schema_file'])
        self.binding = project['root'] / 'taskless-caller.json'
        write_json(self.binding, {'schema': 'poise-caller-binding-1', 'caller_id': 'taskless-writer'})
        self.cwd = project['root'] / 'outside-task'
        self.cwd.mkdir(exist_ok=True)
        if empty:
            assert not self.task_database.exists()
            self.database.unlink()
            create_database(self.database)
        self.cfg['limits']['lock_seconds'] = 0.25
        write_json(self.config, self.cfg)
        self.calls = []

    def invoke(self, operation, body):
        packet = {'operation': operation, 'input': body}
        process = subprocess.run([
            sys.executable, '-B', '-m', 'poise', 'requirements', '--config', str(self.config),
        ], input=json.dumps(packet), capture_output=True, text=True, cwd=self.cwd,
            env=environment(self.binding), timeout=20)
        assert process.stdout.strip(), process.stderr
        value = json.loads(process.stdout)
        self.calls.append({'operation': operation, 'input': body, 'exit': process.returncode,
                           'response': value, 'stderr': process.stderr})
        # Kept as test-owned per-case diagnostics, including the first failed entry.
        write_json(self.project['root'] / 'public-calls.json', self.calls)
        return process.returncode, value

    def query(self):
        code, result = self.invoke('query', {'queries': [{'id': 'registry', 'kind': 'registry'}]})
        assert (code, result['status']) == (0, 'read_only'), result
        return result

    def command(self, key='command'):
        return self.invoke('command', DATA[key])

    def standard_seed(self, count=1):
        from yoyo import read_migrations
        for index in range(count):
            item = DATA['steps'][index]
            migration(self.history, item['name'], item['forward'], item['backward'],
                      depends=[] if index == 0 else [DATA['steps'][index - 1]['name']])
        backend = history_backend(self.database)
        try:
            backend.apply_migrations(backend.to_apply(read_migrations(str(self.history))))
        finally:
            backend.connection.close()

    def assert_taskless(self):
        assert not self.task_database.exists()


def success(response, status='applied'):
    code, value = response
    assert (code, value.get('status')) == (0, status), value
    return value


def expected_empty():
    state = {name: [] for name in COLUMNS}
    state['requirements_meta'] = [(0,)]
    return state


def second_application(tmp_path, monkeypatch):
    from conftest import _source_fixtures
    from projects.helpers import setup_case, bootstrap_fixture_resources
    from poise.composition import project_tools
    fixture_root = tmp_path / 'second-project'
    fixture_root.mkdir()
    project = _source_fixtures.project.__wrapped__(fixture_root, monkeypatch)
    project['root'] = fixture_root / 'independent-setup'
    project['root'].mkdir()
    project['cfg'] = deepcopy(project['cfg'])
    project['cfg'].pop('runtime_resources')
    project['cfg']['project'] = 'application-b'
    write_json(project['root'] / 'config/workflows/development.json', project['workflow'])
    setup, _, packet = setup_case(project)
    packet['edits'][0]['value'] = 'application-b'
    created = project_tools(setup).apply(packet)
    project['config_path'] = Path(created['config_path'])
    project['root'] = project['config_path'].parent
    project['cfg'] = bootstrap_fixture_resources(setup, project['config_path'])
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['requirements_database']
    database.parent.mkdir(parents=True, exist_ok=True)
    create_database(database)
    return Application(project, empty=False)


def assert_command_effect(app, before):
    expected = deepcopy(before)
    expected['requirements'] = sorted(before['requirements'] + [tuple(r) for r in DATA['command_rows']])
    expected['requirement_links'] = sorted(before['requirement_links'] + [('SYS-TASKLESS', 'APP-TASKLESS')])
    expected['requirements_meta'] = [(before['requirements_meta'][0][0] + 1,)]
    assert snapshot(app.database) == expected
    assert (app.history / '0100-taskless.py').is_file()
    public = app.query()
    assert public['revision'] == expected['requirements_meta'][0][0]
    registry = public['results'][0]['value']
    assert registry['requirements'] == {
        row[0]: dict(zip(COLUMNS['requirements'], row, strict=True))
        for row in expected['requirements']
    }
    assert registry['links'] == [
        {'system': row[0], 'application': row[1]} for row in expected['requirement_links']
    ]


def start_lock(app):
    process = subprocess.Popen([
        sys.executable, '-B', str(FIXTURES / 'lock-holder.py'), str(SOURCE), str(app.database),
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=environment(app.binding))
    ready, _, _ = select.select([process.stdout], [], [], 5)
    assert ready and process.stdout.readline().strip() == 'LOCK_READY'
    return process


def _normalized_schema(statement):
    import sqlparse
    return ''.join(
        token.normalized if token.is_keyword else token.value
        for parsed in sqlparse.parse(statement) for token in parsed.flatten()
        if not token.is_whitespace and token.ttype not in sqlparse.tokens.Comment
        and token.value != ';'
    )


def assert_bootstrap_replay(bootstrap, history, target, expected):
    """F01: actual C07 checks generated schema AND replay on a fresh store."""
    from yoyo import read_migrations
    assert not target.exists()
    with sqlite3.connect(target) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(bootstrap.read_text())
        actual_schema = db.execute(DATA['schema_query']).fetchall()
        assert {name: _normalized_schema(ddl) for name, ddl in actual_schema} == {
            item['name']: _normalized_schema(item['sql']) for item in DATA['schema_reference']
        }, 'generated bootstrap differs from independent required schema'
        for table, columns in COLUMNS.items():
            assert [row[1] for row in db.execute(f'PRAGMA table_info({table})')] == list(columns)
    backend = history_backend(target)
    try:
        migrations = read_migrations(str(history))
        backend.apply_migrations(backend.to_apply(migrations))
        assert snapshot(target) == expected
        assert sql(target, 'PRAGMA foreign_key_check') == []
        backend.rollback_migrations(backend.to_rollback(migrations))
        assert snapshot(target) == expected_empty()
        backend.apply_migrations(backend.to_apply(migrations))
        assert snapshot(target) == expected
        assert sql(target, 'PRAGMA foreign_key_check') == []
    finally:
        backend.connection.close()


def application_observation(app):
    """F02: observe existing store history read-only, including absent tables."""
    history = []
    with sqlite3.connect(f'file:{app.database}?mode=ro', uri=True) as db:
        for name, ddl in db.execute(DATA['schema_query']):
            if name in COLUMNS:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = ['"' + row[1].replace('"', '""') + '"'
                       for row in db.execute(f'PRAGMA table_info({quoted})')]
            rows = db.execute(f'SELECT {",".join(columns)} FROM {quoted} ORDER BY rowid').fetchall()
            history.append((name, ddl, rows))
    return snapshot(app.database), file_state(app.project['app']), history


def assert_application_unchanged(app, before):
    """Used by both actual C03 directions and their correction-only controls."""
    assert application_observation(app) == before, 'unselected application state changed'


def finding_controls(output):
    """Exercise the real corrected C07/C03 predicates, outside 19-node RED."""
    from types import SimpleNamespace
    from yoyo import read_migrations
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    expected = {name: [tuple(row) for row in rows]
                for name, rows in DATA['replay_expected'].items()}
    history = output / 'independent-replay-history'
    migration(history, '0300-control-snapshot', DATA['replay_forward'], DATA['replay_backward'])
    valid_statements = [item['sql'] for item in DATA['schema_reference']]
    wrong_statements = [
        'CREATE TABLE requirements (wrong_column TEXT)'
        if statement.lstrip().startswith('CREATE TABLE requirements(') else statement
        for statement in valid_statements
    ]
    assert wrong_statements != valid_statements
    for label, statements in [('valid', valid_statements), ('original-counterexample', wrong_statements)]:
        bootstrap = output / (label + '-bootstrap.sql')
        bootstrap.write_text(';\n'.join(statements) + ';\n')
        try:
            assert_bootstrap_replay(bootstrap, history, output / (label + '.sqlite'), expected)
        except AssertionError as exc:
            assert label == 'original-counterexample', str(exc)
            assert 'independent required schema' in str(exc)
            results['F01_' + label] = {'accepted': False, 'reason': str(exc)}
        else:
            assert label == 'valid', 'wrong-column bootstrap passed the actual C07 assertion'
            results['F01_valid'] = {'accepted': True, 'full_schema_and_history_roundtrip': True}
    # Each direction includes no-history observation, the exact initialized
    # Reviewer counterexample and a valid unchanged-state control.
    for direction in ['A-selected_B-unselected', 'B-selected_A-unselected']:
        directory = output / direction
        directory.mkdir()
        database = directory / 'requirements.sqlite'
        create_database(database)
        app_root = directory / 'application'
        history = app_root / 'docs/requirements/data'
        migration(history, '0100-unselected-existing', ['SELECT 1'], ['SELECT 1'])
        app = SimpleNamespace(database=database, project={'app': app_root})
        bytes_before = database.read_bytes()
        absent = application_observation(app)
        assert absent[2] == []
        assert_application_unchanged(app, absent)
        assert database.read_bytes() == bytes_before
        backend = history_backend(database)
        try:
            migrations = read_migrations(str(history))
            backend.get_applied_migration_hashes()
            initialized = application_observation(app)
            assert initialized[2]
            assert_application_unchanged(app, initialized)
            backend.apply_migrations(backend.to_apply(migrations))
            changed = application_observation(app)
            assert changed[:2] == initialized[:2] and changed[2] != initialized[2]
            for label, before in [('initially-absent', absent), ('original-counterexample', initialized)]:
                try:
                    assert_application_unchanged(app, before)
                except AssertionError:
                    results['F02_' + direction + '_' + label] = {
                        'accepted': False, 'domain_and_files_equal': changed[:2] == before[:2],
                        'history_changed': changed[2] != before[2],
                    }
                else:
                    raise AssertionError('history-only change passed the actual C03 assertion')
        finally:
            backend.connection.close()
        results['F02_' + direction + '_valid'] = {
            'absent_history_accepted_without_observer_mutation': True,
            'initialized_unchanged_accepted': True,
        }
    write_json(output / 'observations.json', results)
    return results
