"""Independent SQLite and external CLI checks for the owned historical migration."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'apps/sequencing/fixtures'
DATA = json.loads((FIXTURES / 'store-migration.json').read_text())
MIGRATION = ROOT / 'apps/sequencing/migrations/v1.py'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def rendered(value, root):
    if isinstance(value, str):
        return value.replace('$ROOT', str(root))
    if isinstance(value, list):
        return [rendered(item, root) for item in value]
    if isinstance(value, dict):
        return {key: rendered(item, root) for key, item in value.items()}
    return value


def migration():
    assert MIGRATION.is_file(), 'T040 historical migration capability is not implemented'
    spec = importlib.util.spec_from_file_location('sequencing_historical_v1_under_test', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


def connect(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys=ON')
    return db


def insert_rows(db, rows, columns):
    for table, values in rows.items():
        names = ','.join(map(quoted, columns[table]))
        slots = ','.join('?' for name in columns[table])
        db.executemany('INSERT INTO ' + quoted(table) + '(' + names + ') VALUES(' + slots + ')', values)


def precursor(db):
    db.executescript((FIXTURES / 'precursor-store.sql').read_text())
    insert_rows(db, DATA['source_rows'], DATA['source_columns'])
    db.commit()


def snapshot(db, columns):
    tables = {}
    for table, fields in columns.items():
        selected = ','.join(map(quoted, fields))
        tables[table] = sorted([list(row) for row in db.execute(
            'SELECT ' + selected + ' FROM ' + quoted(table))], key=canonical)
    return {'tables': tables, 'schema': db.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall(),
        'version': db.execute('PRAGMA user_version').fetchone()[0]}


def assert_rows(db, expected):
    actual = snapshot(db, DATA['columns'])['tables']
    assert actual == {table: sorted(rows, key=canonical) for table, rows in expected.items()}


def assert_schema(db):
    assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert tables == set(DATA['columns'])
    for table, columns in DATA['columns'].items():
        info = db.execute('PRAGMA table_info(' + quoted(table) + ')').fetchall()
        assert {row[1] for row in info} == set(columns), table
        assert [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]] == DATA['primary_keys'][table], table
        groups = {}
        for row in db.execute('PRAGMA foreign_key_list(' + quoted(table) + ')'):
            groups.setdefault(row[0], []).append(row)
        actual = [[rows[0][2], [[row[3], row[4]] for row in sorted(rows, key=lambda row: row[1])]]
                  for rows in groups.values()]
        assert sorted(actual, key=canonical) == sorted(DATA['foreign_keys'][table], key=canonical), table
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def populated(db, root):
    migration()(db)
    db.execute('DELETE FROM meta')
    insert_rows(db, rendered(DATA['target_rows'], root), DATA['columns'])
    db.commit()
    assert_rows(db, rendered(DATA['target_rows'], root))


def table_definitions(db):
    return dict(db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def partial_ddl_observer(db):
    before = table_definitions(db)
    observed = {'creates': [], 'denied': [], 'changed_tables': {}}
    def authorizer(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_CREATE_TABLE and not first.startswith('sqlite_'):
            observed['creates'].append(first)
            current = table_definitions(db)
            changed = {name: sql for name, sql in current.items() if before.get(name) != sql}
            if len(observed['creates']) >= 3 and len(changed) >= 2:
                observed['denied'].append(first)
                observed['changed_tables'] = changed
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorizer, observed


def late_marker_observer(db):
    observed = {}
    def authorizer(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_PRAGMA and first == 'user_version' and second is not None:
            observed['marker'] = (first, second)
            observed['tables'] = table_definitions(db)
            observed['task_columns'] = [row[1] for row in db.execute('PRAGMA table_info(tasks)')]
            observed['tasks'] = [list(row) for row in db.execute(
                'SELECT task_id,requirements_snapshot_revision,state,terminal_json FROM tasks ORDER BY task_id')]
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorizer, observed


def physical_wip(root):
    repository = root / 'source-repository'
    subprocess.run(['git', 'init', '-q', '--initial-branch=fixture', str(repository)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-q', '--allow-empty', '-m', 'Independent fixture'], check=True, capture_output=True)
    worktree = root / 'code one'
    subprocess.run(['git', '-C', str(repository), 'worktree', 'add', '--detach', str(worktree), 'HEAD'],
                   check=True, capture_output=True)
    directories = {Path(row[3]) for row in rendered(DATA['target_rows']['workspace_roots'], root)}
    for directory in sorted(directories):
        relative = str(directory.relative_to(root))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'wip.txt').write_bytes(('Uncommitted material: ' + relative).encode())
    return repository, worktree


def wip_state(root, repository, worktree):
    files = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('wip.txt')}
    directories = {Path(row[3]) for row in rendered(DATA['target_rows']['workspace_roots'], root)}
    return {'files': files,
        'root_names': [(str(path.relative_to(root)), path.is_dir()) for path in sorted(directories)],
        'worktrees': subprocess.check_output(['git', '-C', str(repository), 'worktree', 'list', '--porcelain', '-z']),
        'head': subprocess.check_output(['git', '-C', str(worktree), 'rev-parse', 'HEAD']),
        'dirty': subprocess.check_output(['git', '-C', str(worktree), 'status', '--porcelain'])}


def test_fresh_schema_and_idempotent_reopen(tmp_path):
    path = tmp_path / 'sequencing.sqlite'
    with closing(connect(path)) as db:
        assert migration()(db) is None
        assert not db.in_transaction
        assert_schema(db)
        expected = {table: [] for table in DATA['columns']}
        expected['meta'] = [['revision', 0]]
        assert_rows(db, expected)
        before = snapshot(db, DATA['columns'])
    with closing(connect(path)) as db:
        assert migration()(db) is None
        assert snapshot(db, DATA['columns']) == before
        assert not db.in_transaction


def test_populated_supported_precursor_preserves_references_and_receipts(tmp_path):
    with closing(connect(tmp_path / 'sequencing.sqlite')) as db:
        precursor(db)
        assert migration()(db) is None
        assert not db.in_transaction
        assert_schema(db)
        assert_rows(db, DATA['migrated_rows'])


@pytest.mark.parametrize('case', ['sprint_id', 'claim_json', 'workspace_json', 'terminal_json',
                                 'state', 'unknown-version', 'foreign-format',
                                 'empty-sprint_id', 'empty-claim_json', 'empty-workspace_json', 'empty-terminal_json'])
def test_unsupported_source_refuses_without_loss(tmp_path, case):
    with closing(connect(tmp_path / 'sequencing.sqlite')) as db:
        columns = DATA['source_columns']
        if case == 'foreign-format':
            db.execute('CREATE TABLE foreign_records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('INSERT INTO foreign_records(key,value) VALUES(?,?)', ('preserved', 'Original unrelated data'))
            columns = {'foreign_records': ['key', 'value']}
        else:
            precursor(db)
            if case == 'unknown-version':
                db.execute('PRAGMA user_version=9')
            else:
                field = case.removeprefix('empty-')
                value = '' if case.startswith('empty-') else {
                         'sprint_id': 'SPR-A', 'claim_json': '{"claimant":"S-A"}',
                         'workspace_json': '{"path":"$ROOT/code one"}',
                         'terminal_json': '{"kind":"completed"}', 'state': 'claimed'}[case]
                db.execute('UPDATE tasks SET ' + quoted(field) + '=? WHERE task_id=?', (value, '0043'))
        db.commit()
        before = snapshot(db, columns)
        with pytest.raises(ValueError) as error:
            migration()(db)
        assert str(error.value).strip()
        assert not db.in_transaction
        assert snapshot(db, columns) == before


@pytest.mark.parametrize('source', ['fresh', 'precursor'])
def test_partial_ddl_failure_is_atomic(tmp_path, source):
    with closing(connect(tmp_path / 'sequencing.sqlite')) as db:
        columns = {}
        if source == 'precursor':
            precursor(db)
            columns = DATA['source_columns']
        before = snapshot(db, columns)
        authorizer, progress = partial_ddl_observer(db)
        db.set_authorizer(authorizer)
        try:
            with pytest.raises(sqlite3.Error):
                migration()(db)
        finally:
            db.set_authorizer(None)
        assert len(progress['creates']) >= 3
        assert len(progress['denied']) == 1
        assert len(progress['changed_tables']) >= 2
        assert not db.in_transaction
        assert snapshot(db, columns) == before


def test_late_marker_failure_restores_data_schema_and_physical_wip(tmp_path):
    repository, worktree = physical_wip(tmp_path)
    files_before = wip_state(tmp_path, repository, worktree)
    with closing(connect(tmp_path / 'sequencing.sqlite')) as db:
        precursor(db)
        before = snapshot(db, DATA['source_columns'])
        before_tables = table_definitions(db)
        authorizer, progress = late_marker_observer(db)
        db.set_authorizer(authorizer)
        try:
            with pytest.raises(sqlite3.Error):
                migration()(db)
        finally:
            db.set_authorizer(None)
        assert progress['marker'] == ('user_version', '1')
        assert progress['tables'] != before_tables
        assert set(progress['task_columns']) == set(DATA['columns']['tasks'])
        assert progress['tasks'] == DATA['migrated_rows']['tasks']
        assert not db.in_transaction
        assert snapshot(db, DATA['source_columns']) == before
    assert wip_state(tmp_path, repository, worktree) == files_before


def test_populated_target_history_roots_and_ownership_survive_repeat(tmp_path):
    repository, worktree = physical_wip(tmp_path)
    wip_before = wip_state(tmp_path, repository, worktree)
    path = tmp_path / 'sequencing.sqlite'
    with closing(connect(path)) as db:
        populated(db, tmp_path)
        assert_schema(db)
        before = snapshot(db, DATA['columns'])
    with closing(connect(path)) as db:
        assert migration()(db) is None
        assert not db.in_transaction
        assert_schema(db)
        assert snapshot(db, DATA['columns']) == before
        assert_rows(db, rendered(DATA['target_rows'], tmp_path))
    assert wip_state(tmp_path, repository, worktree) == wip_before


@pytest.mark.parametrize('case', ['active-task', 'active-session', 'double-membership', 'cross-sprint-edge',
                                 'missing-task', 'missing-root-workspace', 'workspace-session', 'workspace-task'])
def test_real_relational_and_ownership_constraints(tmp_path, case):
    with closing(connect(tmp_path / 'sequencing.sqlite')) as db:
        populated(db, tmp_path)
        before = snapshot(db, DATA['columns'])
        db.execute('SAVEPOINT constraint_probe')
        if case in ('double-membership', 'cross-sprint-edge'):
            db.execute('INSERT INTO sprints(sprint_id,revision,state) VALUES(?,?,?)', ('SPR-B', 1, 'draft'))
        if case == 'cross-sprint-edge':
            db.execute('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)', ('SPR-B', '0047'))
        with pytest.raises(sqlite3.IntegrityError):
            if case == 'active-task':
                db.execute('INSERT INTO task_claims(claim_id,task_id,claimant,status) VALUES(?,?,?,?)',
                           ('C-OTHER', '0042', 'S-NEW', 'active'))
            elif case == 'active-session':
                db.execute('INSERT INTO task_claims(claim_id,task_id,claimant,status) VALUES(?,?,?,?)',
                           ('C-OTHER', '0047', 'S-A', 'active'))
            elif case == 'double-membership':
                db.execute('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)', ('SPR-B', '0042'))
            elif case == 'cross-sprint-edge':
                db.execute('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)',
                           ('SPR-A', '0042', '0047', 'completion'))
            elif case == 'missing-task':
                db.execute('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)', ('SPR-A', '9999'))
            elif case == 'missing-root-workspace':
                db.execute('INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
                           ('WS-MISSING', 'code', 0, str(tmp_path / 'other')))
            elif case == 'workspace-session':
                db.execute('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)', ('WS46', 'S-B'))
            else:
                db.execute('INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
                           'VALUES(?,?,?,?,?,?,?)', ('WS-OTHER', '0042', 'repo-1', '5' * 40, 'tasks/other', 'ready', 0))
        db.execute('ROLLBACK TO constraint_probe')
        db.execute('RELEASE constraint_probe')
        assert snapshot(db, DATA['columns']) == before


def cli_root(root):
    (root / 'config').mkdir()
    (root / 'state').mkdir()
    config = root / 'config/sequencing.json'
    config.write_bytes((FIXTURES / 'sequencing-config.json').read_bytes())
    return config, root / 'state/sequencing.sqlite'


def invoke(root, config, packet):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-B', str(ROOT / 'apps/sequencing/bin/sequencing'),
        '--config', str(config)], input=json.dumps(packet, ensure_ascii=False), cwd=root,
        env=environment, text=True, capture_output=True)


def assert_response(process, expected):
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert process.stderr == ''
    assert canonical(json.loads(process.stdout)) == canonical(expected)


def test_cli_precursor_query_and_exact_historical_replay(tmp_path):
    config, path = cli_root(tmp_path)
    with closing(connect(path)) as db:
        precursor(db)
    assert_response(invoke(tmp_path, config, DATA['packets']['source_query']), DATA['responses']['source_query'])
    from apps.sequencing.tests.test_registration import DATA as REGISTRATION, assert_legacy_rows, current_state
    references = REGISTRATION['precursor_all_references']
    assert_response(invoke(tmp_path, config, references['packet']), references['expected'])
    assert_legacy_rows(path, DATA['migrated_rows'])
    before = current_state(path)
    assert_response(invoke(tmp_path, config, DATA['packets']['source_replay']), DATA['responses']['source_replay'])
    assert current_state(path) == before


def test_cli_populated_nonempty_projection_is_complete_and_read_only(tmp_path):
    from apps.sequencing.tests.test_registration import assert_legacy_rows, current_state
    config, path = cli_root(tmp_path)
    repository, worktree = physical_wip(tmp_path)
    with closing(connect(path)) as db:
        populated(db, tmp_path)
    wip_before = wip_state(tmp_path, repository, worktree)
    packet = DATA['packets']['populated_query']
    expected = rendered(DATA['responses']['populated_query'], tmp_path)
    assert_response(invoke(tmp_path, config, packet), expected)
    assert_legacy_rows(path, rendered(DATA['target_rows'], tmp_path))
    before = current_state(path)
    assert_response(invoke(tmp_path, config, packet), expected)
    assert current_state(path) == before
    assert wip_state(tmp_path, repository, worktree) == wip_before


@pytest.mark.parametrize('source', ['unknown-version', 'foreign-format'])
def test_cli_migration_refusal_is_described_and_preserves_wip(tmp_path, source):
    config, path = cli_root(tmp_path)
    repository, worktree = physical_wip(tmp_path)
    wip_before = wip_state(tmp_path, repository, worktree)
    with closing(connect(path)) as db:
        if source == 'unknown-version':
            precursor(db)
            db.execute('PRAGMA user_version=9')
            columns = DATA['source_columns']
        else:
            db.execute('CREATE TABLE foreign_records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('INSERT INTO foreign_records(key,value) VALUES(?,?)', ('original', 'Preserved source'))
            columns = {'foreign_records': ['key', 'value']}
        db.commit()
        before = snapshot(db, columns)
    process = invoke(tmp_path, config, DATA['packets']['source_query'])
    assert process.returncode == 2
    assert process.stderr == ''
    response = json.loads(process.stdout)
    assert set(response) == {'schema', 'status', 'reason'}
    assert response['schema'] == 'stride-sequencing/v1'
    assert response['status'] == 'rejected'
    assert isinstance(response['reason'], str) and response['reason'].strip()
    with closing(connect(path)) as db:
        assert snapshot(db, columns) == before
    assert wip_state(tmp_path, repository, worktree) == wip_before


@pytest.mark.parametrize('subject', [
    'task_provenance',
    'sprint_revision',
    'sprint_history_revision',
    'root_position',
])
@pytest.mark.parametrize('case', ['fraction', 'text', 'first_integer', 'next_integer'])
def test_integer_storage_contract(tmp_path, subject, case):
    value = {'fraction': 1.5, 'text': 'not-an-integer',
             'first_integer': 1, 'next_integer': 2}[case]
    if subject == 'root_position' and case in ('first_integer', 'next_integer'):
        value -= 1
    with closing(connect(tmp_path / 'typed.sqlite')) as db:
        populated(db, tmp_path)
        before = snapshot(db, DATA['columns'])
        db.execute('SAVEPOINT typed_probe')
        try:
            if subject == 'task_provenance':
                sql = ('INSERT INTO tasks(task_id,requirements_snapshot_revision,state) '
                       'VALUES(?,?,?)')
                parameters = ('0051', value, 'available')
                selection = ('SELECT requirements_snapshot_revision,'
                             'typeof(requirements_snapshot_revision) '
                             'FROM tasks WHERE task_id=?')
                key = ('0051',)
            elif subject == 'sprint_revision':
                sql = 'INSERT INTO sprints(sprint_id,revision,state) VALUES(?,?,?)'
                parameters = ('SPR-TYPED', value, 'draft')
                selection = 'SELECT revision,typeof(revision) FROM sprints WHERE sprint_id=?'
                key = ('SPR-TYPED',)
            elif subject == 'sprint_history_revision':
                db.execute('INSERT INTO sprints(sprint_id,revision,state) VALUES(?,?,?)',
                           ('SPR-TYPED', 1, 'draft'))
                sql = ('INSERT INTO sprint_revisions(sprint_id,revision,state,data) '
                       'VALUES(?,?,?,?)')
                parameters = ('SPR-TYPED', value, 'draft', '{}')
                selection = ('SELECT revision,typeof(revision) FROM sprint_revisions '
                             'WHERE sprint_id=?')
                key = ('SPR-TYPED',)
            else:
                sql = ('INSERT INTO workspace_roots(workspace_id,kind,position,path) '
                       'VALUES(?,?,?,?)')
                parameters = ('WS46', 'code', value, str(tmp_path / 'typed-code'))
                selection = ('SELECT position,typeof(position) FROM workspace_roots '
                             'WHERE workspace_id=? AND kind=?')
                key = ('WS46', 'code')
            if case in ('fraction', 'text'):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(sql, parameters)
            else:
                db.execute(sql, parameters)
                assert db.execute(selection, key).fetchall() == [(value, 'integer')]
        finally:
            db.execute('ROLLBACK TO typed_probe')
            db.execute('RELEASE typed_probe')
            assert not db.in_transaction
            assert snapshot(db, DATA['columns']) == before


SOURCE_PROFILE_CLI = json.loads((FIXTURES / 'source-profile-cli.json').read_text())


def profile_source(db, case, *, parents=False):
    source = (FIXTURES / 'precursor-store.sql').read_text()
    replacements = {
        'meta-no-pk': ('key TEXT PRIMARY KEY', 'key TEXT'),
        'requests-no-pk': ('request_id TEXT PRIMARY KEY', 'request_id TEXT'),
        'requests-nullable-digest': ('digest TEXT NOT NULL', 'digest TEXT'),
        'requests-other-type': ('digest TEXT NOT NULL', 'digest INTEGER NOT NULL'),
        'check': ('response_json TEXT NOT NULL',
                  'response_json TEXT NOT NULL, CHECK(length(request_id)<64)'),
        'collation': ('request_id TEXT PRIMARY KEY',
                      'request_id TEXT COLLATE NOCASE PRIMARY KEY'),
        'foreign-key': ('response_json TEXT NOT NULL',
                        'response_json TEXT NOT NULL, FOREIGN KEY(digest) REFERENCES meta(key)'),
    }
    if case in replacements:
        before, after = replacements[case]
        assert source.count(before) == 1
        source = source.replace(before, after)
    if case == 'formatted':
        for table in ('meta', 'tasks', 'requests'):
            before = 'CREATE TABLE ' + table
            assert source.count(before) == 1
            source = source.replace(before, 'CREATE /* historical table */ TABLE\n "' + table + '"')
    columns = {}
    if case not in ('user-prefix-only', 'view-only'):
        db.executescript(source)
        for table in ('meta', 'tasks', 'requests'):
            if table == 'requests' and parents:
                db.executemany('INSERT INTO meta(key,value) VALUES(?,?)',
                               [(row[1], 0) for row in DATA['source_rows']['requests']])
            insert_rows(db, {table: DATA['source_rows'][table]}, DATA['source_columns'])
        columns = dict(DATA['source_columns'])
    if case in ('user-prefix-only', 'user-prefix-precursor'):
        db.execute('CREATE TABLE sqliteXforeign(value TEXT)')
        db.execute('INSERT INTO sqliteXforeign(value) VALUES(?)', ('Preserved user source',))
        columns['sqliteXforeign'] = ['value']
    if case == 'view-only':
        db.execute('CREATE VIEW source_view AS SELECT 7 AS value')
    elif case == 'precursor-view':
        db.execute('CREATE VIEW source_view AS SELECT task_id FROM tasks')
    elif case == 'precursor-trigger':
        db.execute('CREATE TRIGGER source_trigger AFTER UPDATE ON tasks BEGIN SELECT 1; END')
    elif case == 'precursor-index':
        db.execute('CREATE INDEX source_index ON tasks(state)')
    db.commit()
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    return columns


@pytest.mark.parametrize('case', [
    'user-prefix-only', 'user-prefix-precursor', 'view-only', 'precursor-view',
    'precursor-trigger', 'precursor-index', 'meta-no-pk', 'requests-no-pk',
    'requests-nullable-digest', 'requests-other-type',
])
def test_source_profile_refusal_preserves_catalog_and_wip(tmp_path, case):
    repository, worktree = physical_wip(tmp_path)
    wip_before = wip_state(tmp_path, repository, worktree)
    with closing(connect(tmp_path / 'source.sqlite')) as db:
        columns = profile_source(db, case)
        before = snapshot(db, columns)
        with pytest.raises(ValueError) as error:
            migration()(db)
        assert str(error.value).strip()
        assert not db.in_transaction
        assert snapshot(db, columns) == before
    assert wip_state(tmp_path, repository, worktree) == wip_before


@pytest.mark.parametrize('case', ['canonical', 'formatted'])
def test_source_profile_accepts_internal_indexes_and_equivalent_ddl(tmp_path, case):
    with closing(connect(tmp_path / 'source.sqlite')) as db:
        profile_source(db, case)
        assert db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                          "WHERE type='index' ORDER BY name").fetchall() == [
            ('index', 'sqlite_autoindex_meta_1', 'meta', None),
            ('index', 'sqlite_autoindex_requests_1', 'requests', None),
            ('index', 'sqlite_autoindex_tasks_1', 'tasks', None),
        ]
        assert migration()(db) is None
        assert not db.in_transaction
        assert_schema(db)
        assert_rows(db, DATA['migrated_rows'])


@pytest.mark.parametrize('case', ['check', 'collation', 'foreign-key'])
def test_retained_source_constraints_refuse_with_valid_cli_control(tmp_path, case):
    control = tmp_path / 'canonical'
    control.mkdir()
    config, path = cli_root(control)
    with closing(connect(path)) as db:
        profile_source(db, 'canonical', parents=(case == 'foreign-key'))
    packet = SOURCE_PROFILE_CLI['packets'][case]
    if case == 'collation':
        old_ids = [row[0] for row in DATA['source_rows']['requests']]
        assert packet['input']['request_id'] not in old_ids
        assert packet['input']['request_id'].casefold() in [value.casefold() for value in old_ids]
    elif case == 'check':
        assert len(packet['input']['request_id']) == 70
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'apps/sequencing/bin/sequencing'), '--config', str(config)],
        input=json.dumps(packet) + '\n', cwd=control, env=environment, text=True, capture_output=True,
    )
    assert_response(process, SOURCE_PROFILE_CLI['response'])
    from apps.sequencing.tests.test_registration import DATA as REGISTRATION, current_state
    expected_read = REGISTRATION['legacy_control_read']
    assert_response(invoke(control, config, expected_read['packet']), expected_read['expected'])
    assert current_state(path)['version'] == 2
    with closing(connect(path)) as db:
        assert db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone() == (3,)
    changed = tmp_path / 'changed'
    changed.mkdir()
    repository, worktree = physical_wip(changed)
    wip_before = wip_state(changed, repository, worktree)
    with closing(connect(changed / 'source.sqlite')) as db:
        columns = profile_source(db, case, parents=(case == 'foreign-key'))
        before = snapshot(db, columns)
        with pytest.raises(ValueError) as error:
            migration()(db)
        assert str(error.value).strip()
        assert not db.in_transaction
        assert snapshot(db, columns) == before
    assert wip_state(changed, repository, worktree) == wip_before
