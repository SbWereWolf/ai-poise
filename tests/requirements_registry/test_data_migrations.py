"""Requirements persist only by applying independent, literal DML histories."""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from requirements_registry.migration_support import (
    Scene, COLUMNS, FIXTURES, create_database, populate_database, schema_fixture,
    schema_b_database, migration, snapshot, sql,
)
from requirements_registry.fixtures.native_migrations.isolated_replay import history_backend

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


def literal_dependencies(path):
    assignments = {node.targets[0].id: node.value for node in ast.parse(path.read_text()).body
                   if isinstance(node, ast.Assign)}
    return set(ast.literal_eval(assignments['__depends__']))


def arrange_dependent_history(scene):
    previous = '0009-prior'
    for name, pairs in [('0100-create', ORDERED_STEPS[:1]), ('0101-rename', ORDERED_STEPS[1:2]),
                        ('0102-link', ORDERED_STEPS[2:])]:
        migration(scene.data, name, [pair[0] for pair in pairs], [pair[1] for pair in pairs], depends=[previous])
        previous = name
    applied(scene.invoke('apply', {'kind': 'data'}))


def snapshot_b(path):
    result = snapshot(path)
    result['requirements'] = sql(path, 'SELECT id,level,status,text,note FROM requirements ORDER BY id')
    return result


def schema_signature(path):
    import sqlparse
    with sqlite3.connect(path) as db:
        definitions = db.execute('SELECT name,sql FROM sqlite_schema WHERE type=? ORDER BY name', ('table',)).fetchall()
        return {name: ''.join(token.normalized if token.is_keyword else token.value
                              for statement in sqlparse.parse(ddl) for token in statement.flatten()
                              if not token.is_whitespace and token.ttype not in sqlparse.tokens.Comment)
                for name, ddl in definitions if name in COLUMNS}


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


def source_state(root):
    excluded = {'.git', '.runtime', '__pycache__', '.pytest_cache'}
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and not (set(path.relative_to(root).parts) & excluded)}


def effects(scene, foreign=None):
    result = {'rows': snapshot(scene.database), 'journal': journal_state(scene.database),
              'primary': source_state(scene.project['app']), 'worktree': source_state(scene.worktree)}
    if foreign is not None:
        result['foreign'] = source_state(foreign)
    return result


def isolated_replay(schema, data, target):
    process = subprocess.run([
        sys.executable, '-I', '-S', '-B', str(FIXTURES / 'isolated_replay.py'),
        str(Path(__file__).resolve().parents[2] / '.runtime/dependencies'),
        str(schema), str(data), str(target),
    ], text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.fixture
def scene(project):
    return Scene(project)


def test_migration_fixture_uses_real_owned_task(scene, tmp_path):
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
    reference = tmp_path / 'reference-b.sqlite'
    schema_b_database(reference)
    state = snapshot_b(reference)
    assert all(state[table] for table in COLUMNS)
    assert len(state['requirements']) == 4
    signature = schema_signature(reference)
    assert set(signature) == set(COLUMNS)
    # Exercise actual standard-only fixtures before any missing native feature.
    data_a = tmp_path / 'isolated-a-data'
    data_a.mkdir()
    for suffix in ('sql', 'rollback.sql'):
        (data_a / ('0001.' + suffix)).write_bytes((FIXTURES / ('standard-data.' + suffix)).read_bytes())
    target_a = tmp_path / 'isolated-a.sqlite'
    isolated_replay(scene.schema, data_a, target_a)
    expected_a = {name: [] for name in COLUMNS}
    expected_a['requirements_meta'] = [(0,)]
    expected_a['requirements'] = [('STANDARD', 'system', 'current', 'Standard SQL; COMMIT remains text')]
    assert snapshot(target_a) == expected_a
    reference_a = tmp_path / 'reference-a.sqlite'
    create_database(reference_a)
    assert schema_signature(target_a) == schema_signature(reference_a)
    schema_b = tmp_path / 'isolated-b-schema'
    schema_fixture(schema_b)
    migration(schema_b, '0002-note', ['ALTER TABLE requirements ADD COLUMN note TEXT'],
              ['ALTER TABLE requirements DROP COLUMN note'], depends=['0001'])
    data_b = tmp_path / 'isolated-b-data'
    data_b.mkdir()
    for suffix in ('sql', 'rollback.sql'):
        (data_b / ('0001.' + suffix)).write_bytes((FIXTURES / ('schema-b-data.' + suffix)).read_bytes())
    target_b = tmp_path / 'isolated-b.sqlite'
    isolated_replay(schema_b, data_b, target_b)
    assert snapshot_b(target_b) == state
    assert schema_signature(target_b) == signature
    from yoyo import read_migrations
    parent_b = tmp_path / 'parent-b.sqlite'
    schema_backend = history_backend(parent_b, 'schema')
    schema_backend.apply_migrations(schema_backend.to_apply(read_migrations(str(schema_b))))
    data_backend = history_backend(parent_b, 'data')
    data_migrations = read_migrations(str(data_b))
    data_backend.apply_migrations(data_backend.to_apply(data_migrations))
    assert snapshot_b(parent_b) == state
    data_backend.rollback_migrations(data_backend.to_rollback(data_migrations))
    empty_b = {name: [] for name in COLUMNS}
    empty_b['requirements_meta'] = [(0,)]
    assert snapshot_b(parent_b) == empty_b
    data_backend.apply_migrations(data_backend.to_apply(data_migrations))
    assert snapshot_b(parent_b) == state and schema_signature(parent_b) == signature
    for target in (target_a, target_b, parent_b):
        assert sql(target, 'SELECT migration_id,operation FROM _fixture_data_log ORDER BY created_at_utc,rowid') == [
            ('0001', 'apply'), ('0001', 'rollback'), ('0001', 'apply')]
        expected_schema = [('0001', 'apply')] + ([('0002-note', 'apply')] if target != target_a else [])
        assert sql(target, 'SELECT migration_id,operation FROM _fixture_schema_log ORDER BY created_at_utc,rowid') == expected_schema
    from copy import deepcopy
    from conftest import write_json
    second_config = deepcopy(scene.cfg)
    second_config['paths']['requirements_database'] = str(reference)
    second_config['paths']['requirements_lock'] = str(reference.with_suffix('.lock'))
    write_json(scene.project['config_path'], second_config)
    try:
        code, view = scene.invoke('query', {'queries': [{'id': 'coverage', 'kind': 'coverage', 'mode': 'current'}]})
        assert code == 0 and view['status'] == 'read_only' and view['revision'] == 17
    finally:
        write_json(scene.project['config_path'], scene.cfg)
    assert snapshot(scene.database)['requirements_meta'] == [(1,)]
    sql(reference, 'DROP TABLE requirement_actions')
    assert schema_signature(reference) != signature


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


def test_schema_and_data_histories_with_same_id_are_independent(scene):
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


def test_two_configured_stores_have_isolated_interleaved_histories(scene):
    from copy import deepcopy
    from conftest import write_json
    first_cfg = deepcopy(scene.cfg)
    first_database = scene.database
    first_data = scene.data
    second_database = scene.database.with_name('second-requirements.sqlite')
    second_schema = scene.project['app'] / 'second-schema-history'
    second_relative = Path('docs/second-requirements/data')
    second_data = scene.worktree / second_relative
    schema_fixture(second_schema)
    second_data.mkdir(parents=True)
    second_cfg = deepcopy(first_cfg)
    second_cfg['paths'].update(
        requirements_database=str(second_database),
        requirements_lock=str(second_database.with_suffix('.lock')),
        requirements_schema_migrations=str(second_schema),
        requirements_data_migrations=str(scene.project['app'] / second_relative),
        requirements_schema_file=str(scene.project['app'] / 'docs/second-requirements/schema.sql'),
    )
    first_database.unlink()
    applied(scene.invoke('apply', {'kind': 'schema'}))
    migration(first_data, '0001', [COMMANDS['create']['sql']], [COMMANDS['create']['rollback_sql']])
    applied(scene.invoke('apply', {'kind': 'data'}))
    first_rows, first_journal = snapshot(first_database), journal_state(first_database)
    assert first_rows['requirements'] == [('TEMP', 'system', 'current', 'before')]
    assert first_journal
    write_json(scene.project['config_path'], second_cfg)
    applied(scene.invoke('apply', {'kind': 'schema'}))
    migration(second_data, '0001', [
        "INSERT INTO requirements(id,level,status,text) VALUES('TEMP','system','current','second store')"],
        [COMMANDS['create']['rollback_sql']])
    applied(scene.invoke('apply', {'kind': 'data'}))
    second_rows, second_journal = snapshot(second_database), journal_state(second_database)
    assert second_rows['requirements'] == [('TEMP', 'system', 'current', 'second store')]
    assert second_journal
    assert snapshot(first_database) == first_rows and journal_state(first_database) == first_journal
    applied(scene.invoke('rollback', {'kind': 'data'}))
    empty_second, rolled_second_journal = snapshot(second_database), journal_state(second_database)
    assert empty_second['requirements'] == []
    assert snapshot(first_database) == first_rows and journal_state(first_database) == first_journal
    write_json(scene.project['config_path'], first_cfg)
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(first_database)['requirements'] == []
    assert snapshot(second_database) == empty_second and journal_state(second_database) == rolled_second_journal
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(first_database) == first_rows
    assert snapshot(second_database) == empty_second and journal_state(second_database) == rolled_second_journal
    write_json(scene.project['config_path'], second_cfg)
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(second_database) == second_rows
    assert snapshot(first_database) == first_rows


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
    isolated_replay(scene.schema, scene.data, isolated)
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
    arrange_dependent_history(scene)
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
    assert literal_dependencies(scene.data / consolidated[0]) == {'0009-prior'}
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
    arrange_dependent_history(scene)
    before = snapshot(scene.database)
    pairs = ORDERED_STEPS
    applied(scene.invoke('squash', {'name': '0200-combined',
                                  'migrations': ['0102-link', '0100-create', '0101-rename']}))
    assert scene.files().keys() == {'0009-prior.py', '0200-combined.py'}
    assert prior.read_bytes() == before_prior
    assert literal_steps(scene.data / '0200-combined.py') == pairs
    assert literal_dependencies(scene.data / '0200-combined.py') == {'0009-prior'}
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
    populate_database(scene.database)
    applied(scene.invoke('command', COMMANDS['create']))
    migration(scene.schema, '0002-note', ['ALTER TABLE requirements ADD COLUMN note TEXT'],
              ['ALTER TABLE requirements DROP COLUMN note'], depends=['0001'])
    applied(scene.invoke('apply', {'kind': 'schema'}))
    notes = json.loads((FIXTURES / 'schema-b-notes.json').read_text())
    for identifier, note in notes.items():
        sql(scene.database, 'UPDATE requirements SET note=? WHERE id=?', (note, identifier))
    expected = {table: sorted(map(tuple, rows)) for table, rows in
                json.loads((FIXTURES / 'data.json').read_text()).items()}
    expected['requirements'] = sorted([tuple(row) + (notes[row[0]],) for row in expected['requirements']] +
                                      [('TEMP', 'system', 'current', 'before', notes['TEMP'])])
    before = snapshot_b(scene.database)
    assert all(before[table] for table in COLUMNS)
    assert before == expected
    applied(scene.invoke('rebase', {'name': '0200-schema-b'}))
    assert list(scene.files()) == ['0200-schema-b.py']
    pairs = literal_steps(scene.data / '0200-schema-b.py')
    assert any('note' in forward and 'New-schema value' in forward for forward, _ in pairs)
    import sqlparse
    assert all(statement.get_type() in {'INSERT', 'UPDATE', 'DELETE'}
               for forward, backward in pairs for direction in (forward, backward)
               for statement in sqlparse.parse(direction))
    assert snapshot_b(scene.database) == expected
    assert sql(scene.database, 'SELECT note FROM requirements WHERE id=?', ('TEMP',)) == [('New-schema value',)]
    bootstrap = scene.worktree / scene.bootstrap_relative
    assert list(bootstrap.parent.glob('*.sql')) == [bootstrap]
    bootstrap_db = tmp_path / 'bootstrap.sqlite'
    with sqlite3.connect(bootstrap_db) as db:
        db.executescript(bootstrap.read_text())
        assert [row[1] for row in db.execute('PRAGMA table_info(requirements)')] == [
            'id', 'level', 'status', 'text', 'note']
    reference = tmp_path / 'reference-b.sqlite'
    create_database(reference)
    sql(reference, 'ALTER TABLE requirements ADD COLUMN note TEXT')
    assert set(schema_signature(bootstrap_db)) == set(COLUMNS)
    assert schema_signature(bootstrap_db) == schema_signature(reference)
    from yoyo import read_migrations
    fresh = tmp_path / 'fresh.sqlite'
    schema_backend = history_backend(fresh, 'schema')
    schema_backend.apply_migrations(schema_backend.to_apply(read_migrations(str(scene.schema))))
    data_backend = history_backend(fresh, 'data')
    data_backend.apply_migrations(data_backend.to_apply(read_migrations(str(scene.data))))
    assert snapshot_b(fresh) == expected
    assert sql(fresh, 'SELECT note FROM requirements WHERE id=?', ('TEMP',)) == [('New-schema value',)]
    data_backend.rollback_migrations(data_backend.to_rollback(read_migrations(str(scene.data))))
    empty_b = {table: [] for table in COLUMNS}
    empty_b['requirements_meta'] = [(0,)]
    assert snapshot_b(fresh) == empty_b
    data_backend.apply_migrations(data_backend.to_apply(read_migrations(str(scene.data))))
    assert snapshot_b(fresh) == expected
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


def test_import_bootstrap_cannot_persist_through_removed_public_writer(scene):
    # Empty revision0 ensures this valid historical payload reaches the old writer.
    from requirements_registry.migration_support import INSERT_ORDER
    with sqlite3.connect(scene.database) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for table in reversed(INSERT_ORDER):
            db.execute(f'DELETE FROM {table}')
        db.execute('INSERT INTO requirements_meta(revision) VALUES(?)', (0,))
    source = FIXTURES / 'import-bootstrap.json'
    before = effects(scene)
    original = source.read_bytes()
    code, value = scene.invoke('import_bootstrap', {'path': str(source), 'request_id': 'direct-import'})
    assert code != 0 and value['status'] == 'rejected'
    assert 'import_bootstrap' in value['reason']
    assert value['recovery'].strip()
    assert effects(scene) == before and source.read_bytes() == original


@pytest.mark.parametrize('operation', ['export', 'squash'])
@pytest.mark.parametrize('selection', [[], 'all', {}, 7], ids=['empty', 'string', 'object', 'integer'])
def test_invalid_explicit_selection_never_means_all(project, tmp_path, operation, selection):
    scene = Scene(project, prior=True)
    applied(scene.invoke('apply', {'kind': 'data'}))
    applied(scene.invoke('command', COMMANDS['create']))
    source = tmp_path / 'selection-source.sqlite'
    create_database(source, populated=True)
    source_before = (snapshot(source), journal_state(source), source.read_bytes())
    before = effects(scene)
    if operation == 'export':
        body = {'source': str(source), 'requirements': selection, 'name': '0200-selection'}
    else:
        body = {'migrations': selection, 'name': '0200-selection'}
    code, value = scene.invoke(operation, body)
    assert code != 0 and value['status'] == 'rejected'
    assert any(word in value['reason'].lower() for word in ('nonempty', 'непуст', 'selection', 'выбор'))
    assert value['recovery'].strip()
    assert effects(scene) == before
    assert (snapshot(source), journal_state(source), source.read_bytes()) == source_before


@pytest.mark.parametrize('setting', [
    'requirements_schema_migrations', 'requirements_data_migrations', 'requirements_schema_file',
])
@pytest.mark.parametrize('invalid', [None, 0, []], ids=['null', 'integer', 'array'])
def test_invalid_migration_path_type_has_no_effects(scene, setting, invalid):
    from conftest import write_json
    scene.cfg['paths'][setting] = invalid
    write_json(scene.project['config_path'], scene.cfg)
    before = effects(scene)
    code, value = scene.invoke('command', COMMANDS['create'])
    assert code != 0 and value['status'] == 'rejected'
    assert setting in value['reason'] and value['recovery'].strip()
    assert effects(scene) == before


@pytest.mark.parametrize('field,invalid', [
    ('name', None), ('name', 7), ('sql', ''), ('sql', None), ('sql', []),
    ('rollback_sql', ''), ('rollback_sql', None), ('rollback_sql', 7),
], ids=['name-null', 'name-integer', 'sql-empty', 'sql-null', 'sql-array',
        'rollback-empty', 'rollback-null', 'rollback-integer'])
def test_invalid_current_command_fields_have_causal_refusals(scene, field, invalid):
    body = dict(COMMANDS['create'])
    body[field] = invalid
    before = effects(scene)
    code, value = scene.invoke('command', body)
    assert code != 0 and value['status'] == 'rejected'
    assert field in value['reason'].lower() and value['recovery'].strip()
    assert effects(scene) == before


@pytest.mark.parametrize('operation', ['apply', 'rollback'])
@pytest.mark.parametrize('body', [{}, {'kind': None}, {'kind': []}, {'kind': 'other'}],
                         ids=['missing', 'null', 'array', 'unsupported'])
def test_invalid_history_kind_has_causal_refusal(scene, operation, body):
    before = effects(scene)
    code, value = scene.invoke(operation, body)
    assert code != 0 and value['status'] == 'rejected'
    assert 'kind' in value['reason'].lower() and value['recovery'].strip()
    assert effects(scene) == before


def test_foreign_claimed_caller_cannot_write_another_task_worktree(scene):
    binding, runtime, context = scene.foreign_context()
    assert context['task'] == 'T2' and context['stage'] == 'tests'
    assert runtime.session != scene.runtime.session
    foreign = Path(context['worktree'])
    assert foreign != scene.worktree and foreign.is_dir()
    before = effects(scene, foreign)
    code, value = scene.invoke('command', COMMANDS['create'], binding=binding)
    assert code != 0 and value['status'] == 'rejected'
    assert any(word in value['reason'].lower() for word in ('foreign', 'чуж', 'context', 'контекст', 'worktree'))
    assert value['recovery'].strip() and effects(scene, foreign) == before


def test_readonly_owned_context_cannot_write_requirements(project):
    scene = Scene(project, read_only=True)
    assert scene.context['stage'] == 'tests'
    assert scene.context['workflow']['stage_contract']['allowed_paths'] == []
    before = effects(scene)
    code, value = scene.invoke('command', COMMANDS['create'])
    assert code != 0 and value['status'] == 'rejected'
    assert any(word in value['reason'].lower() for word in ('readonly', 'read-only', 'чтени'))
    assert value['recovery'].strip() and effects(scene) == before


def test_standard_sql_history_applies_rolls_back_and_reapplies(scene):
    before = snapshot(scene.database)
    for filename in ('standard-data.sql', 'standard-data.rollback.sql'):
        (scene.data / filename).write_bytes((FIXTURES / filename).read_bytes())
    files = scene.files()
    applied(scene.invoke('apply', {'kind': 'data'}))
    expected = dict(before)
    expected['requirements'] = sorted(before['requirements'] + [
        ('STANDARD', 'system', 'current', 'Standard SQL; COMMIT remains text')])
    assert snapshot(scene.database) == expected
    applied(scene.invoke('rollback', {'kind': 'data'}))
    assert snapshot(scene.database) == before
    applied(scene.invoke('apply', {'kind': 'data'}))
    assert snapshot(scene.database) == expected and scene.files() == files
    assert [(name, operation) for name, operation in journal_operations(scene.database)
            if name == 'standard-data'] == [
        ('standard-data', 'apply'), ('standard-data', 'rollback'), ('standard-data', 'apply')]
