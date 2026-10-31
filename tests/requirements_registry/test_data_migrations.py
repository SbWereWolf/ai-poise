"""Requirements persist only by applying independent, literal DML histories."""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from requirements_registry.migration_support import Scene, COLUMNS, FIXTURES, create_database, migration, snapshot, sql

COMMANDS = json.loads((FIXTURES / 'commands.json').read_text())
ORDERED_STEPS = [tuple(row) for row in json.loads((FIXTURES / 'ordered-steps.json').read_text())]


def applied(response):
    code, value = response
    assert (code, value.get('status')) == (0, 'applied'), value
    return value


def generated(response):
    code, value = response
    assert (code, value.get('status')) == (0, 'generated'), value
    return value


def literal_steps(path):
    tree = ast.parse(path.read_text())
    assert all(isinstance(node, (ast.ImportFrom, ast.Assign)) for node in tree.body)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert len(imports) == 1 and imports[0].module == 'yoyo'
    assert [(alias.name, alias.asname) for alias in imports[0].names] == [('step', None)]
    assignments = {node.targets[0].id: node.value for node in tree.body if isinstance(node, ast.Assign)}
    assert ast.literal_eval(assignments['__transactional__']) is True
    ast.literal_eval(assignments['__depends__'])
    assert isinstance(assignments['steps'], ast.List)
    pairs = []
    for call in assignments['steps'].elts:
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'step'
        assert len(call.args) == 2 and not call.keywords
        pair = tuple(ast.literal_eval(value) for value in call.args)
        assert all(isinstance(value, str) and value.strip() for value in pair)
        pairs.append(pair)
    return pairs


def journal_state(database):
    """Include metadata-table creation as an effect, as well as journal rows."""
    state = []
    with sqlite3.connect(database) as db:
        tables = db.execute('SELECT name,sql FROM sqlite_schema WHERE type=? ORDER BY name', ('table',)).fetchall()
        for name, ddl in tables:
            if name in COLUMNS:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = ['"' + row[1].replace('"', '""') + '"' for row in db.execute(f'PRAGMA table_info({quoted})')]
            state.append((name, ddl, db.execute(f'SELECT {",".join(columns)} FROM {quoted} ORDER BY rowid').fetchall()))
    return state


@pytest.fixture
def scene(project):
    return Scene(project)


def test_migration_fixture_uses_real_owned_task(scene):
    assert scene.context['stage'] == 'tests'
    assert scene.worktree.is_dir()
    assert scene.worktree != scene.project['app']
    assert not scene.database.is_relative_to(scene.worktree)
    assert scene.data.is_relative_to(scene.worktree)
    assert set(snapshot(scene.database)) == set(COLUMNS)
    environment = dict(scene.environment(), POISE_CONFIG=str(scene.project['config_path']))
    process = subprocess.run([sys.executable, '-B', '-m', 'poise', 'work'],
        input=json.dumps({'operation': 'show', 'input': {'queries': [{'id': 'task', 'kind': 'task'}]}, 'messages': []}),
        text=True, capture_output=True, env=environment, cwd=scene.worktree, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    response = json.loads(Path(json.loads(process.stdout)['response_path']).read_text())
    assert response['results'][0]['value']['stage'] == 'tests'


def test_native_command_creates_one_literal_file_and_applies_it(scene):
    before = snapshot(scene.database)
    applied(scene.invoke('command', COMMANDS['create']))
    files = scene.files()
    assert list(files) == ['0100-create.py']
    assert literal_steps(scene.data / '0100-create.py') == [
        (COMMANDS['create']['sql'], COMMANDS['create']['rollback_sql'])]
    expected = dict(before)
    expected['requirements'] = sorted(before['requirements'] + [('TEMP', 'system', 'current', 'before')])
    assert snapshot(scene.database) == expected
    # Re-applying applied files must have no new business effects.
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == expected
    assert scene.files() == files
    assert not list((scene.project['app'] / scene.data_relative).iterdir())


def test_add_change_link_and_delete_have_reversible_files(scene):
    initial = snapshot(scene.database)
    for name in ('create', 'rename', 'link'):
        applied(scene.invoke('command', COMMANDS[name]))
    assert sql(scene.database, 'SELECT system_id,application_id FROM requirement_links ORDER BY system_id') == [
        ('FIXTURE-SYSTEM', 'FIXTURE-APPLICATION'), ('RENAMED', 'CHILD')]
    final = snapshot(scene.database)
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(scene.database) == initial
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == final
    assert len(scene.files()) == 3


def test_quoted_transaction_and_ddl_words_are_data(scene):
    initial = snapshot(scene.database)
    applied(scene.invoke('command', COMMANDS['quoted']))
    assert sql(scene.database, 'SELECT text FROM requirements WHERE id=?', ('WORDS',)) == [
        ("COMMIT; ROLLBACK; CREATE TABLE; кавычка ' и\nстрока",)]
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(scene.database) == initial


@pytest.mark.parametrize('direction', ['sql', 'rollback_sql'])
@pytest.mark.parametrize('control', ['COMMIT', 'ROLLBACK'])
def test_transaction_escape_is_rejected_before_any_effect(scene, direction, control):
    before = snapshot(scene.database)
    journal = journal_state(scene.database)
    files = scene.files()
    packet = dict(COMMANDS['create'])
    packet[direction] += '; ' + control
    code, value = scene.invoke('command', packet)
    assert code != 0 and value['status'] == 'rejected'
    assert 'transaction' in value['reason'].lower() or 'транзакц' in value['reason'].lower()
    assert value['recovery'].strip()
    assert snapshot(scene.database) == before
    assert scene.files() == files
    assert journal_state(scene.database) == journal


@pytest.mark.parametrize('extension', ['py', 'sql'])
@pytest.mark.parametrize('operation', ['apply', 'rollback'])
def test_standard_nontransactional_file_is_rejected_and_byte_preserved(scene, extension, operation):
    path = scene.data / ('0099-invalid.' + extension)
    if extension == 'py':
        migration(scene.data, '0099-invalid', ['SELECT 1'], ['SELECT 1'], transactional=False)
    else:
        path.write_text('-- transactional: false\nSELECT 1;\n')
        (scene.data / '0099-invalid.rollback.sql').write_text('SELECT 1;\n')
    before = snapshot(scene.database)
    journal = journal_state(scene.database)
    files = scene.files()
    code, value = scene.invoke(operation, {'kind': 'data'})
    assert code != 0 and value['status'] == 'rejected'
    assert 'transaction' in value['reason'].lower() or 'транзакц' in value['reason'].lower()
    assert value['recovery'].strip()
    assert snapshot(scene.database) == before
    assert scene.files() == files
    assert journal_state(scene.database) == journal


def test_failing_second_data_statement_rolls_back_first_statement(scene):
    before = snapshot(scene.database)
    code, value = scene.invoke('command', COMMANDS['partial_apply'])
    assert code != 0 and value['status'] == 'rejected'
    assert 'missing_table' in value['reason']
    assert snapshot(scene.database) == before
    assert list(scene.files()) == ['0100-partial.py']


def test_failing_rollback_is_one_transaction(scene):
    applied(scene.invoke('command', COMMANDS['create']))
    migration(scene.data, '0100-create', ['SELECT 1', COMMANDS['create']['sql']],
              ['DELETE FROM missing_table', "DELETE FROM requirements WHERE id='TEMP'"])
    before = snapshot(scene.database)
    code, value = scene.invoke('rollback', {'kind': 'data'})
    assert code != 0 and value['status'] == 'rejected'
    assert 'missing_table' in value['reason']
    assert snapshot(scene.database) == before


def test_ddl_cannot_enter_requirement_data_history(scene):
    before = snapshot(scene.database)
    files = scene.files()
    code, value = scene.command('0100-ddl', 'CREATE TABLE forbidden(id TEXT)', 'DROP TABLE forbidden')
    assert code != 0 and value['status'] == 'rejected'
    assert 'DDL' in value['reason'] or 'схем' in value['reason']
    assert scene.files() == files and snapshot(scene.database) == before


def test_unclaimed_caller_cannot_write_in_primary_checkout(scene):
    before = snapshot(scene.database)
    code, value = scene.invoke('command', COMMANDS['create'], owned=False)
    assert code != 0 and value['status'] == 'rejected'
    assert 'task' in value['reason'].lower() or 'задач' in value['reason'].lower()
    assert snapshot(scene.database) == before and scene.files() == {}


def test_duplicate_migration_name_does_not_overwrite_source_or_data(scene):
    applied(scene.invoke('command', COMMANDS['create']))
    before, files = snapshot(scene.database), scene.files()
    packet = dict(COMMANDS['create'], sql="DELETE FROM requirements WHERE id='FIXTURE-SYSTEM'")
    code, value = scene.invoke('command', packet)
    assert code != 0 and value['status'] == 'rejected'
    assert '0100-create' in value['reason']
    assert snapshot(scene.database) == before and scene.files() == files


@pytest.mark.parametrize('store_label', ['first-store', 'second-store'])
def test_schema_and_data_histories_with_same_id_are_independent(scene, store_label):
    # The initialized database belongs exclusively to this temporary case.
    scene.database.unlink()
    applied(scene.invoke('apply', {'kind': 'schema'}))
    assert set(snapshot(scene.database)) == set(COLUMNS)
    migration(scene.data, '0001', [COMMANDS['create']['sql']], [COMMANDS['create']['rollback_sql']])
    applied(scene.invoke('apply', {'kind': 'data'}))
    expected = {table: [] for table in COLUMNS}
    expected['requirements'] = [('TEMP', 'system', 'current', 'before')]
    expected['requirements_meta'] = [(0,)]
    assert snapshot(scene.database) == expected
    code, value = scene.invoke('rollback', {'kind': 'schema'})
    assert code != 0 and value['status'] == 'rejected'
    assert snapshot(scene.database) == expected
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(scene.database)['requirements'] == []
    applied(scene.invoke('rollback', {'kind': 'schema'}))
    with sqlite3.connect(scene.database) as db:
        tables = {row[0] for row in db.execute('SELECT name FROM sqlite_schema WHERE type=?', ('table',))}
    assert not (set(COLUMNS) & tables)
    applied(scene.invoke('apply', {'kind': 'schema'}))
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == expected


@pytest.mark.parametrize('direction', ['apply', 'rollback'])
def test_schema_failure_rolls_back_earlier_ddl(scene, direction):
    # A separate standard schema migration makes DDL atomicity directly visible.
    if direction == 'apply':
        migration(scene.schema, '0002-failure', ['CREATE TABLE schema_probe(id TEXT)',
                  'INSERT INTO missing_table(id) VALUES(1)'], ['DROP TABLE schema_probe', 'SELECT 1'], depends=['0001'])
        scene.database.unlink()
    else:
        scene.database.unlink()
        applied(scene.invoke('apply', {'kind': 'schema'}))
        migration(scene.schema, '0002-failure', ['SELECT 1', 'CREATE TABLE schema_probe(id TEXT)'],
                  ['DELETE FROM missing_table', 'DROP TABLE schema_probe'], depends=['0001'])
        applied(scene.invoke('apply', {'kind': 'schema'}))
    code, value = scene.invoke(direction, {'kind': 'schema'})
    assert code != 0 and value['status'] == 'rejected'
    assert 'missing_table' in value['reason']
    with sqlite3.connect(scene.database) as db:
        tables = {row[0] for row in db.execute('SELECT name FROM sqlite_schema WHERE type=?', ('table',))}
    assert ('schema_probe' in tables) is (direction == 'rollback')


def test_full_export_rebuilds_all_nine_tables_and_preserves_source(scene, tmp_path):
    source = tmp_path / 'source.sqlite'
    create_database(source, populated=True)
    original_bytes = source.read_bytes()
    expected = {table: sorted(map(tuple, rows)) for table, rows in
                json.loads((FIXTURES / 'data.json').read_text()).items()}
    assert snapshot(source) == expected
    scene.database.unlink()
    applied(scene.invoke('apply', {'kind': 'schema'}))
    empty = snapshot(scene.database)
    generated(scene.invoke('export', {'source': str(source), 'requirements': None, 'name': '0001'}))
    assert snapshot(scene.database)['requirements'] == []  # Generation does not apply it.
    assert list(scene.files()) == ['0001.py']
    literal_steps(scene.data / '0001.py')
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == expected
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(scene.database) == empty
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == expected
    assert source.read_bytes() == original_bytes
    isolated = tmp_path / 'isolated.sqlite'
    replay = subprocess.run([
        sys.executable, '-I', '-B', str(FIXTURES / 'isolated_replay.py'),
        str(Path(__file__).resolve().parents[2] / '.runtime/dependencies'),
        str(scene.schema), str(scene.data), str(isolated),
    ], text=True, capture_output=True, timeout=30)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert snapshot(isolated) == expected


def test_selected_export_keeps_ancestor_and_domain_closure(scene, tmp_path):
    source = tmp_path / 'source.sqlite'
    create_database(source, populated=True)
    before = snapshot(scene.database)
    original_bytes = source.read_bytes()
    generated(scene.invoke('export', {'source': str(source), 'requirements': ['APP-A'], 'name': '0100-transfer'}))
    assert snapshot(scene.database) == before
    applied(scene.invoke('apply', {'kind': 'data'}))
    actual = snapshot(scene.database)
    fixture = json.loads((FIXTURES / 'data.json').read_text())
    assert actual['requirements'] == sorted(before['requirements'] + [tuple(row) for row in fixture['requirements'][:2]])
    assert actual['requirement_links'] == sorted(before['requirement_links'] + [('SYS-A', 'APP-A')])
    for table in ('domain_areas', 'domain_entities', 'domain_actions', 'requirement_entities', 'requirement_actions'):
        assert actual[table] == [tuple(row) for row in fixture[table]]
    assert actual['requirement_requests'] == before['requirement_requests']
    assert actual['requirements_meta'] == before['requirements_meta']
    assert source.read_bytes() == original_bytes


def test_selected_export_does_not_duplicate_exact_existing_ancestor(scene, tmp_path):
    source = tmp_path / 'source.sqlite'
    create_database(source, populated=True)
    row = json.loads((FIXTURES / 'data.json').read_text())['requirements'][0]
    sql(scene.database, 'INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)', row)
    generated(scene.invoke('export', {'source': str(source), 'requirements': ['APP-A'], 'name': '0100-transfer'}))
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert sql(scene.database, 'SELECT id,level,status,text FROM requirements WHERE id=?', ('SYS-A',)) == [tuple(row)]
    assert sql(scene.database, 'SELECT id FROM requirements WHERE id=?', ('APP-A',)) == [('APP-A',)]


@pytest.mark.parametrize('problem', ['missing-source', 'missing-requirement', 'conflict'])
def test_transfer_failure_does_not_change_target_or_files(scene, tmp_path, problem):
    source = tmp_path / 'source.sqlite'
    create_database(source, populated=True)
    if problem == 'conflict':
        sql(scene.database, 'INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)',
            ('APP-A', 'application', 'current', 'Conflicting target text'))
    before, files = snapshot(scene.database), scene.files()
    code, value = scene.invoke('export', {
        'source': str(source if problem != 'missing-source' else tmp_path / 'absent.sqlite'),
        'requirements': ['ABSENT'] if problem == 'missing-requirement' else ['APP-A'],
        'name': '0100-transfer',
    })
    assert code != 0 and value['status'] == 'rejected'
    assert value['reason'].strip() and value['recovery'].strip()
    assert {'missing-source': 'absent.sqlite', 'missing-requirement': 'ABSENT', 'conflict': 'APP-A'}[problem] in value['reason']
    assert snapshot(scene.database) == before and scene.files() == files
    assert not (tmp_path / 'absent.sqlite').exists()


def journal_operations(database):
    """Observe standard log columns without prescribing private table names."""
    result = []
    with sqlite3.connect(database) as db:
        tables = db.execute('SELECT name FROM sqlite_schema WHERE type=? ORDER BY name', ('table',)).fetchall()
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = {row[1] for row in db.execute(f'PRAGMA table_info({quoted})')}
            if {'migration_id', 'operation', 'created_at_utc'} <= columns:
                result.extend(db.execute(
                    f'SELECT migration_id,operation FROM {quoted} ORDER BY created_at_utc,rowid'
                ).fetchall())
    return result


def test_task_completion_consolidates_literal_order_before_source_freeze(project):
    # Prior tracked source belongs to an earlier completed Task and is immutable.
    scene = Scene(project, prior=True)
    prior = scene.data / '0009-prior.py'
    prior_bytes = prior.read_bytes()
    applied(scene.invoke('apply', {'kind': 'data'}))
    for name in ('create', 'rename', 'link'):
        applied(scene.invoke('command', COMMANDS[name]))
    original_pairs = ORDERED_STEPS
    before = snapshot(scene.database)
    old_log = journal_operations(scene.database)
    assert old_log  # Real standard tool; rows alone cannot prove its algorithm.
    outcome = scene.complete()
    assert outcome['status'] == 'handoff_completed', outcome
    files = scene.files()
    assert files['0009-prior.py'] == prior_bytes
    consolidated = [name for name in files if name != '0009-prior.py']
    assert len(consolidated) == 1
    assert literal_steps(scene.data / consolidated[0]) == original_pairs
    assert snapshot(scene.database) == before
    added = journal_operations(scene.database)[len(old_log):]
    rollbacks = [name for name, operation in added if operation == 'rollback']
    assert rollbacks[:3] == ['0102-link', '0101-rename', '0100-create']
    new_id = Path(consolidated[0]).stem
    assert [(name, operation) for name, operation in added if name == new_id] == [
        (new_id, 'apply'), (new_id, 'rollback'), (new_id, 'apply')]
    assert not [(name, operation) for name, operation in added if name == '0009-prior']
    verification = outcome['verification']
    from conftest import git
    frozen = git(scene.worktree, 'ls-tree', '-r', '--name-only', verification['verification_commit'])
    assert str(scene.data_relative / consolidated[0]) in frozen.splitlines()
    assert all(str(scene.data_relative / (COMMANDS[name]['name'] + '.py')) not in frozen.splitlines()
               for name in ('create', 'rename', 'link'))
    replay_log = journal_operations(scene.database)
    replay = scene.complete()
    assert replay['status'] == 'handoff_completed' and replay['replayed'] is True
    assert scene.files() == files and snapshot(scene.database) == before
    assert journal_operations(scene.database) == replay_log


def test_completion_without_requirement_changes_does_not_create_migrations(scene):
    before, files = snapshot(scene.database), scene.files()
    outcome = scene.complete()
    assert outcome['status'] == 'handoff_completed', outcome
    assert snapshot(scene.database) == before and scene.files() == files


def test_completion_blocks_on_actual_rollback_failure(scene):
    applied(scene.invoke('command', COMMANDS['create']))
    migration(scene.data, '0100-create', ['SELECT 1', COMMANDS['create']['sql']],
              ['DELETE FROM missing_table', "DELETE FROM requirements WHERE id='TEMP'"])
    before = snapshot(scene.database)
    outcome = scene.complete()
    assert outcome['status'] != 'handoff_completed'
    assert 'missing_table' in json.dumps(outcome, ensure_ascii=False)
    assert snapshot(scene.database) == before
    assert '0100-create.py' in scene.files()


def test_explicit_squash_uses_original_steps_and_leaves_other_migrations(project):
    scene = Scene(project, prior=True)
    prior = scene.data / '0009-prior.py'
    before_prior = prior.read_bytes()
    applied(scene.invoke('apply', {'kind': 'data'}))
    for name in ('create', 'rename', 'link'):
        applied(scene.invoke('command', COMMANDS[name]))
    before = snapshot(scene.database)
    pairs = ORDERED_STEPS
    applied(scene.invoke('squash', {'name': '0200-combined',
                                  'migrations': ['0102-link', '0100-create', '0101-rename']}))
    assert scene.files().keys() == {'0009-prior.py', '0200-combined.py'}
    assert prior.read_bytes() == before_prior
    assert literal_steps(scene.data / '0200-combined.py') == pairs
    assert snapshot(scene.database) == before
    applied(scene.invoke('rollback', {'kind': 'data'}))
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == before


def test_squash_unknown_selection_does_not_change_anything(scene):
    applied(scene.invoke('command', COMMANDS['create']))
    before, files = snapshot(scene.database), scene.files()
    code, value = scene.invoke('squash', {'name': '0200-combined', 'migrations': ['absent']})
    assert code != 0 and value['status'] == 'rejected'
    assert 'absent' in value['reason']
    assert snapshot(scene.database) == before and scene.files() == files


def test_schema_transition_replaces_active_dml_and_bootstrap_preserving_new_values(scene, tmp_path):
    scene.database.unlink()
    applied(scene.invoke('apply', {'kind': 'schema'}))
    applied(scene.invoke('command', COMMANDS['create']))
    migration(scene.schema, '0002-note', ['ALTER TABLE requirements ADD COLUMN note TEXT'],
              ['ALTER TABLE requirements DROP COLUMN note'], depends=['0001'])
    applied(scene.invoke('apply', {'kind': 'schema'}))
    sql(scene.database, 'UPDATE requirements SET note=? WHERE id=?', ('New-schema value', 'TEMP'))
    before = snapshot(scene.database)
    applied(scene.invoke('rebase', {'name': '0200-schema-b'}))
    assert list(scene.files()) == ['0200-schema-b.py']
    pairs = literal_steps(scene.data / '0200-schema-b.py')
    assert any('note' in forward and 'New-schema value' in forward for forward, _ in pairs)
    import sqlparse
    assert all(statement.get_type() in {'INSERT', 'UPDATE', 'DELETE'}
               for forward, backward in pairs for direction in (forward, backward)
               for statement in sqlparse.parse(direction))
    assert snapshot(scene.database) == before
    assert sql(scene.database, 'SELECT note FROM requirements WHERE id=?', ('TEMP',)) == [('New-schema value',)]
    bootstrap = scene.worktree / scene.bootstrap_relative
    assert list(bootstrap.parent.glob('*.sql')) == [bootstrap]
    with sqlite3.connect(tmp_path / 'bootstrap.sqlite') as db:
        db.executescript(bootstrap.read_text())
        assert [row[1] for row in db.execute('PRAGMA table_info(requirements)')] == [
            'id', 'level', 'status', 'text', 'note']
    from yoyo import get_backend, read_migrations
    fresh = tmp_path / 'fresh.sqlite'
    schema_backend = get_backend('sqlite:///' + str(fresh), migration_table='_fixture_schema')
    schema_backend.apply_migrations(schema_backend.to_apply(read_migrations(str(scene.schema))))
    data_backend = get_backend('sqlite:///' + str(fresh), migration_table='_fixture_data')
    data_backend.apply_migrations(data_backend.to_apply(read_migrations(str(scene.data))))
    assert snapshot(fresh) == before
    assert sql(fresh, 'SELECT note FROM requirements WHERE id=?', ('TEMP',)) == [('New-schema value',)]
    data_backend.rollback_migrations(data_backend.to_rollback(read_migrations(str(scene.data))))
    data_backend.apply_migrations(data_backend.to_apply(read_migrations(str(scene.data))))
    assert snapshot(fresh) == before
    assert sql(fresh, 'SELECT note FROM requirements WHERE id=?', ('TEMP',)) == [('New-schema value',)]


@pytest.mark.parametrize('setting', [
    'requirements_schema_migrations', 'requirements_data_migrations', 'requirements_schema_file',
])
def test_missing_migration_setting_fails_before_source_or_database_effects(scene, setting):
    from conftest import write_json
    del scene.cfg['paths'][setting]
    write_json(scene.project['config_path'], scene.cfg)
    before, files = snapshot(scene.database), scene.files()
    code, value = scene.invoke('command', COMMANDS['create'])
    assert code != 0 and value['status'] == 'rejected'
    assert setting in value['reason'] and value['recovery'].strip()
    assert snapshot(scene.database) == before and scene.files() == files


def test_unknown_configuration_key_does_not_change_current_operation(scene):
    from conftest import write_json
    scene.cfg['unrelated_future_setting'] = {'unknown': 'ignored'}
    write_json(scene.project['config_path'], scene.cfg)
    applied(scene.invoke('command', COMMANDS['create']))
    assert sql(scene.database, 'SELECT id FROM requirements WHERE id=?', ('TEMP',)) == [('TEMP',)]


def test_data_path_outside_stage_scope_is_rejected(scene):
    from conftest import write_json
    outside = scene.project['app'] / 'src/forbidden-data'
    scene.cfg['paths']['requirements_data_migrations'] = str(outside)
    write_json(scene.project['config_path'], scene.cfg)
    before = snapshot(scene.database)
    code, value = scene.invoke('command', COMMANDS['create'])
    assert code != 0 and value['status'] == 'rejected'
    assert 'scope' in value['reason'].lower() or 'област' in value['reason'].lower()
    assert snapshot(scene.database) == before
    assert not outside.exists() and not (scene.worktree / 'src/forbidden-data').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'bad/name', ''])
def test_invalid_migration_name_has_specific_refusal_without_effects(scene, name):
    before, files = snapshot(scene.database), scene.files()
    packet = dict(COMMANDS['create'], name=name)
    code, value = scene.invoke('command', packet)
    assert code != 0 and value['status'] == 'rejected'
    assert 'name' in value['reason'].lower() or 'имя' in value['reason'].lower()
    assert snapshot(scene.database) == before and scene.files() == files


def test_direct_bulk_write_is_not_a_public_persistence_path(scene):
    before, files = snapshot(scene.database), scene.files()
    code, value = scene.invoke('apply', {
        'request_id': 'direct-bulk', 'expected_revision': 1,
        'operations': [{'kind': 'put_requirement', 'requirement': {
            'id': 'DIRECT', 'level': 'system', 'status': 'current', 'text': 'Must not write directly.'}}],
    })
    assert code != 0 and value['status'] == 'rejected'
    assert not any(word in value['reason'].lower() for word in ('legacy', 'старый', 'совместим'))
    assert snapshot(scene.database) == before and scene.files() == files


def test_public_query_remains_readonly_without_claimed_task(scene):
    before, files = snapshot(scene.database), scene.files()
    code, value = scene.invoke('query', {'queries': [
        {'id': 'coverage', 'kind': 'coverage', 'mode': 'current'},
    ]}, owned=False)
    assert code == 0 and value['status'] == 'read_only'
    assert value['revision'] == 1
    assert value['results'] == [{'id': 'coverage', 'value': {
        'application_without_system': [], 'system_without_application': []}}]
    assert snapshot(scene.database) == before and scene.files() == files
