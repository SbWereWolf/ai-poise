"""Public Work task-by-ID/slug read contracts on independently seeded SQLite.

Integration subject: packet grammar, Work show, runtime read projections and
their real SQLite/CLI boundaries. Oracles are literal test-owned fixtures.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import re
import sqlite3
from threading import Event, Thread

import pytest

from poise.common import PoiseError
from poise.infrastructure.sqlite.database import Database
from poise.modules.work.domain import parse_request

from .helpers import batch_config
from .task_addressed_support import (
    addressed, assert_values, cli, encode, fixture, initialize, insert_task,
    metadata, packet, read_tools, seed_retired, seed_scene, snapshot,
)


@pytest.mark.parametrize('needle', ['READ-ALPHA', 'ALPHA'])
def test_addressed_packet_admits_exact_id_and_fragment(needle):
    value = packet(needle)
    try:
        actual = parse_request(value, batch_config())
    except PoiseError as error:
        pytest.fail(f'ADDRESS_QUERY_REJECTED: {error}', pytrace=False)
    assert actual == value


@pytest.mark.parametrize('value', fixture('invalid-inputs.json'))
def test_addressed_packet_rejects_invalid_selector_with_remedy(value):
    with pytest.raises(PoiseError) as caught:
        parse_request(packet(value), batch_config())
    reason = str(caught.value)
    assert 'task_id' in reason, 'ADDRESS_INPUT_DIAGNOSTIC_MISSING: ' + reason
    assert re.search(r'непуст|строк|nonempty|string', reason, re.IGNORECASE), reason


def test_addressed_packet_keeps_unknown_field_rejection():
    value = packet()
    value['input']['queries'][0]['unexpected'] = True
    with pytest.raises(PoiseError):
        parse_request(value, batch_config())


def test_fixture_readers_accept_independent_contracts(project):
    path, _ = seed_scene(project)
    tools = read_tools(project)
    assert tools.runtime.task_queries.record('READ-ALPHA')['goal'] == 'Адресат A.'
    assert tools.runtime.task_queries.record('CURRENT-OTHER')['goal'] == 'Текущая задача читателя.'
    assert tools.runtime.task_commands.workflow_context('READ-ALPHA')['role'] == 'executor'
    assert tools.runtime.task_commands.workflow_context('CURRENT-OTHER')['role'] == 'reviewer'
    assert tools.runtime.task_commands.workflow_context('READ-BETA')['role'] == 'executor'
    assert tools.runtime.task_commands.content_context('READ-ALPHA') == fixture('oracles.json')['content']
    before = snapshot(path)
    value = packet()
    value['input']['queries'][0].pop('task_id')
    actual = tools.invoke(value)['results'][0]['value']
    assert actual['task'] == 'CURRENT-OTHER'
    assert actual['stage'] == 'reader_step'
    assert snapshot(path) == before


def test_invalid_selector_diagnostic_preserves_real_business_state(project):
    path, _ = seed_scene(project)
    tools = read_tools(project)
    before = snapshot(path)
    with pytest.raises(PoiseError) as caught:
        tools.invoke(packet(None))
    reason = str(caught.value)
    assert snapshot(path) == before
    assert 'task_id' in reason, 'ADDRESS_INPUT_DIAGNOSTIC_MISSING: ' + reason


@pytest.mark.parametrize('case', fixture('lookup-cases.json'), ids=lambda case: case['needle'])
def test_slug_resolution_is_literal_exact_first_and_explicit(project, case):
    path, _ = seed_scene(project)
    tools = read_tools(project)
    before = snapshot(path)
    actual = addressed(tools, case['needle'])
    assert actual['status'] == case['status']
    assert actual['query'] == case['needle']
    if case['status'] == 'found':
        assert actual['match'] == case['match']
        assert actual['context']['id'] == case['id']
    else:
        assert 'context' not in actual
        assert [item['id'] for item in actual['matches']] == case['ids']
        expected = fixture('oracles.json')
        if case['status'] == 'ambiguous':
            assert actual['matches'] == expected['ambiguous']
            assert actual['next_action'] == expected['ambiguous_action']
        else:
            assert actual['matches'] == []
            assert actual['next_action'] == expected['not_found_action']
    assert snapshot(path) == before
    assert addressed(tools, case['needle']) == actual
    assert snapshot(path) == before


@pytest.mark.parametrize('reader_current', [False, True])
def test_addressed_context_is_target_owned_and_preserves_every_business_row(project, reader_current):
    path, _ = seed_scene(project, reader_current=reader_current)
    tools = read_tools(project)
    expected = fixture('oracles.json')
    before = snapshot(path)
    actual = addressed(tools)['context']
    assert_values(actual, expected['active'])
    assert_values(actual['stage'], expected['stage'])
    assert actual['content'] == expected['content']
    assert_values(actual['feedback'], expected['feedback'])
    assert snapshot(path) == before


@pytest.mark.parametrize('state', ['available', 'active', 'verified', 'accepted', 'completed', 'cancelled'])
def test_addressed_read_preserves_saved_lifecycle_state(project, state):
    path, _ = initialize(project)
    actor = 'foreign-owner' if state in ('active', 'verified', 'accepted') else None
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        insert_task(db, 'STATE-TARGET', 'Сохранённое состояние.', state=state, actor=actor)
        db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('reader',))
        db.commit()
    tools = read_tools(project)
    before = snapshot(path)
    result = addressed(tools, 'STATE-TARGET')['context']
    expected = fixture('state-context.json')
    expected.update(status=state, claimed_by=actor)
    assert_values(result, expected)
    if state in ('completed', 'cancelled'):
        assert result['historical'] is True
        assert result['stage'] is None
        assert result['result_commit'] == 'a' * 40
    else:
        assert result['stage']['id'] == 'work'
    assert snapshot(path) == before


@pytest.mark.parametrize('typed', [False, True])
def test_incomplete_newborn_is_read_as_saved_without_execution_assumptions(project, typed):
    path, _ = initialize(project)
    draft = {'goal': 'Неполный черновик.'}
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        insert_task(db, 'DRAFT-TARGET', 'Неполный черновик.', state='newborn', actor='draft-owner')
        if typed:
            draft['goal_type'] = 'development'
            value = fixture('newborn-metadata.json')
            value['newborn']['draft'] = draft
            value['workflow'] = fixture('active-metadata.json')['workflow']
            db.execute('UPDATE tasks SET metadata=? WHERE id=?', (encode(value), 'DRAFT-TARGET'))
        db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('reader',))
        db.commit()
    tools = read_tools(project)
    before = snapshot(path)
    context = addressed(tools, 'DRAFT-TARGET')['context']
    assert_values(context, fixture('newborn-context.json'))
    assert context['draft'] == draft
    assert context['stage'] is None
    assert 'requirements' not in context['draft']
    assert 'definition_of_done' not in context['draft']
    assert snapshot(path) == before


@pytest.mark.parametrize('state', ['completed', 'cancelled'])
def test_retired_terminal_read_uses_saved_delivery_without_restoring_materials(project, state):
    path, _ = initialize(project)
    root = project['root'] / 'never-restored-task-root'
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        insert_task(db, 'RETIRED-TARGET', 'Историческая задача.', state=state)
        seed_retired(db, 'RETIRED-TARGET', root)
        db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('reader',))
        db.commit()
    tools = read_tools(project)
    assert tools.runtime.delivery_tools.query('RETIRED-TARGET')['status'] == 'delivery_complete'
    before = snapshot(path)
    context = addressed(tools, 'RETIRED-TARGET')['context']
    assert context['id'] == 'RETIRED-TARGET'
    assert context['status'] == state and context['historical'] is True
    assert context['delivery']['status'] == 'delivery_complete'
    assert context['stage'] is None and context['result_commit'] == 'a' * 40
    assert not root.exists()
    assert snapshot(path) == before


def test_mixed_packet_addresses_two_targets_without_rebinding_current(project):
    path, _ = seed_scene(project)
    tools = read_tools(project)
    request = fixture('mixed-request.json')
    before = snapshot(path)
    try:
        result = tools.invoke(request)
    except PoiseError as error:
        pytest.fail(f'ADDRESS_QUERY_REJECTED: {error}', pytrace=False)
    assert [item['id'] for item in result['results']] == ['selected', 'second', 'current']
    first, second, current = [item['value'] for item in result['results']]
    assert first['context']['id'] == 'READ-ALPHA'
    assert second['context']['id'] == 'READ-BETA'
    assert_values(second['context'], fixture('oracles.json')['beta'])
    assert_values(second['context']['stage'], fixture('oracles.json')['beta_stage'])
    assert current['task'] == 'CURRENT-OTHER'
    assert snapshot(path) == before


def test_addressed_read_reports_real_storage_failure_separately_from_no_match(project):
    path, _ = seed_scene(project)
    tools = read_tools(project)
    before = snapshot(path)
    held = path.with_name('held-fixture-database.sqlite')
    path.rename(held)
    try:
        with pytest.raises(PoiseError) as caught:
            tools.invoke(packet('MISSING-ID'))
        reason = str(caught.value)
        if "required fields ['id', 'kind']" in reason:
            pytest.fail('ADDRESS_QUERY_REJECTED: ' + reason, pytrace=False)
        assert re.search('хранилищ|database|read-only|БД', reason, re.IGNORECASE)
        assert 'not_found' not in reason
        assert not path.exists()
    finally:
        held.rename(path)
    assert snapshot(path) == before


def test_actual_cli_keeps_all_matches_in_full_saved_response(project):
    path, _ = seed_scene(project, reader_current=False, extra=False)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for index in range(40):
            insert_task(db, f'BULK-{index:03}', f'Совпадение {index:03}.')
        db.commit()
    old_request = packet()
    old_request['input']['queries'][0].pop('task_id')
    primer, _, _ = cli(project, old_request)
    assert primer.returncode == 0, primer.stdout + primer.stderr
    config = json.loads(project['config_path'].read_text())
    config['limits']['output_chars'] = 1200
    project['config_path'].write_text(json.dumps(config, ensure_ascii=False))
    before = snapshot(path)
    process, envelope, full = cli(project, packet('BULK-'))
    assert process.returncode == 0, 'ADDRESS_CLI_REJECTED: ' + process.stdout + process.stderr
    assert envelope['response_path']
    assert len(process.stdout) <= 1200
    assert full['status'] == 'read_only'
    result = full['results'][0]['value']
    assert result['status'] == 'ambiguous'
    assert result['matches'] == fixture('bulk-matches.json')
    assert len(encode(full)) > 1200
    assert snapshot(path) == before


def test_actual_cli_found_context_comes_from_explicit_selected_installation(project, tmp_path, monkeypatch):
    from conftest import project as project_fixture
    seed_scene(project, reader_current=False, extra=False)
    second_root = tmp_path / 'second-installation'
    second_root.mkdir()
    other = project_fixture.__wrapped__(second_root, monkeypatch)
    other_path, _ = seed_scene(other, reader_current=False, extra=False)
    with closing(sqlite3.connect(other_path)) as db:
        row = db.execute('SELECT metadata FROM tasks WHERE id=?', ('READ-ALPHA',)).fetchone()
        value = json.loads(row[0])
        value['goal'] = value['contract']['goal'] = 'Адресат другого проекта.'
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (encode(value), 'READ-ALPHA'))
        db.commit()
    request = packet()
    current_request = deepcopy(request)
    current_request['input']['queries'][0].pop('task_id')
    for selected in (project, other):
        primer, _, _ = cli(selected, current_request)
        assert primer.returncode == 0, primer.stdout + primer.stderr
    from .task_addressed_support import state_path
    before_first, before_other = snapshot(state_path(project)), snapshot(other_path)
    process, _, full = cli(other, request)
    assert process.returncode == 0, 'ADDRESS_CLI_REJECTED: ' + process.stdout + process.stderr
    context = full['results'][0]['value']['context']
    assert context['id'] == 'READ-ALPHA'
    assert context['goal'] == 'Адресат другого проекта.'
    assert snapshot(state_path(project)) == before_first
    assert snapshot(other_path) == before_other


class _ObservedCursor:
    """Forward real SQLite results; mark only an actually nonempty candidate read."""
    def __init__(self, cursor, observation):
        self.cursor, self.observation = cursor, observation

    def _seen(self, value, rows):
        if any(any(cell == 'COHERENT-A' for cell in row) for row in rows):
            self.observation['candidate'] = True
        return value

    def fetchone(self):
        value = self.cursor.fetchone()
        return self._seen(value, [] if value is None else [value])

    def fetchall(self):
        value = self.cursor.fetchall()
        return self._seen(value, value)

    def fetchmany(self, *args):
        value = self.cursor.fetchmany(*args)
        return self._seen(value, value)

    def __iter__(self):
        return self

    def __next__(self):
        value = next(self.cursor)
        return self._seen(value, [value])

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class _ObservedConnection:
    def __init__(self, connection, observation):
        self.connection, self.observation = connection, observation

    def execute(self, sql, parameters=()):
        return _ObservedCursor(self.connection.execute(sql, parameters), self.observation)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.mark.parametrize('filtered', [False, True])
def test_snapshot_cursor_observes_real_candidates_for_both_sql_forms(filtered):
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO tasks(id) VALUES(?)', ('COHERENT-A',))
        observation = {'candidate': False}
        observed = _ObservedConnection(db, observation)
        empty = observed.execute('SELECT id FROM tasks WHERE id=?', ('COHERENT-',)).fetchall()
        assert empty == [] and observation == {'candidate': False}
        cursor = (observed.execute('SELECT id FROM tasks WHERE instr(id, ?) > 0', ('COHERENT-',))
                  if filtered else observed.execute('SELECT id FROM tasks ORDER BY id'))
        assert observation == {'candidate': False}
        rows = cursor.fetchall()
        assert [tuple(row) for row in rows] == [('COHERENT-A',)]
        assert all(isinstance(row, sqlite3.Row) for row in rows)
        assert observation == {'candidate': True}


def test_fragment_resolution_and_context_share_one_coherent_snapshot(project, monkeypatch):
    path, _ = initialize(project)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        insert_task(db, 'COHERENT-A', 'Адресат A.', actor='owner-alpha')
        db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('reader',))
        db.commit()
    tools = read_tools(project)
    boundary, committed, abort = Event(), Event(), Event()
    results = {}
    original = Database.read_transaction
    observation = {'fired': False}

    @contextmanager
    def coordinated(database):
        local = {'candidate': False}
        with original(database) as db:
            yield _ObservedConnection(db, local)
        # The real snapshot worker and its original-file OFD protection are
        # already released. Waiting for the writer inside that boundary deadlocks.
        if local['candidate'] and not observation['fired']:
            observation['fired'] = True
            boundary.set()
            assert committed.wait(8), 'SNAPSHOT_WRITER_DID_NOT_FINISH'
            assert 'error' not in results, results.get('error')

    def writer():
        try:
            assert boundary.wait(8), 'SNAPSHOT_CANDIDATE_NOT_OBSERVED'
            if abort.is_set():
                return
            with closing(sqlite3.connect(path, timeout=2)) as db:
                db.execute('PRAGMA foreign_keys=ON')
                changed = metadata('COHERENT-A', 'После записи A.')
                changed['contract']['requirements'] = ['Требование после записи.']
                changed['workflow']['stages'][0]['instruction'] = 'Инструкция после записи.'
                db.execute('UPDATE tasks SET metadata=?,version=18 WHERE id=?',
                           (encode(changed), 'COHERENT-A'))
                insert_task(db, 'COHERENT-B', 'После записи B.', actor='owner-after')
                db.commit()
            results['expected_state'] = snapshot(path)
        except BaseException as error:
            results['error'] = repr(error)
        finally:
            committed.set()

    monkeypatch.setattr(Database, 'read_transaction', coordinated)
    worker = Thread(target=writer)
    worker.start()
    try:
        result = addressed(tools, 'COHERENT-')
        assert observation['fired'], 'SNAPSHOT_COORDINATION_NOT_OBSERVED'
        assert committed.is_set() and 'error' not in results
        if result['status'] == 'found':
            assert result['query'] == 'COHERENT-' and result['match'] == 'substring'
            expected = fixture('oracles.json')
            expected['active']['id'] = 'COHERENT-A'
            expected['active']['branch'] = 'tasks/COHERENT-A'
            context = result['context']
            assert_values(context, expected['active'])
            assert_values(context['stage'], expected['stage'])
            assert context['content'] == expected['content']
            assert_values(context['feedback'], expected['feedback'])
        else:
            assert result['status'] == 'ambiguous' and result['query'] == 'COHERENT-'
            assert result['matches'] == fixture('oracles.json')['coherent_after']
            assert 'context' not in result
        assert snapshot(path) == results['expected_state']
    finally:
        abort.set()
        boundary.set()
        worker.join(10)
        assert not worker.is_alive(), 'SNAPSHOT_WRITER_LEAKED'
