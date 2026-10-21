"""Real source-to-target schema/data observations; no production oracle."""
import sqlite3
import pytest
from store_checks import (CONNECT, initial_records,
                          installation, original_rows, prepare, snapshot,
                          stored_records)


@pytest.mark.parametrize('source', [False, True], ids=['fresh', 'nonempty-t020'])
def test_prepares_owned_format_and_preserves_existing_data(tmp_path, source):
    _, store, path, _ = installation(tmp_path, source=source)
    before = original_rows(path) if source else None
    prepare(store)
    with CONNECT(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1, 'Existing connect must prepare the owned Execution storage format'
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'execution_records' in names
    if source:
        assert original_rows(path) == before
        assert stored_records(path) == initial_records()
    else:
        assert stored_records(path) == []


def test_repeated_preparation_is_a_stable_reopen(tmp_path):
    _, store, path, _ = installation(tmp_path, source=True)
    prepare(store)
    before = snapshot(path)
    prepare(store)
    assert snapshot(path) == before
    assert stored_records(path) == initial_records()


def test_missing_process_body_is_preserved_as_a_reference(tmp_path):
    _, store, path, _ = installation(tmp_path, source=True)
    prepare(store)
    actual = store.read_records('0042')
    assert actual == [initial_records()[0]]
    assert actual[0]['data']['contract']['process_ref']['revision'] == 'a' * 64
    assert all(item['kind'] != 'process_snapshot' for item in stored_records(path))


def test_preparation_failure_rolls_back_actual_schema_and_data(tmp_path, monkeypatch):
    module, store, path, _ = installation(tmp_path, source=True)
    before = snapshot(path)
    observed = []

    def interrupted_connection(*args, **kwargs):
        connection = CONNECT(*args, **kwargs)

        def trace(sql):
            text = sql.strip().upper()
            if text.startswith('CREATE TABLE') and 'EXECUTION_RECORDS' in text:
                observed.append(text)

        def authorize(action, table, *unused):
            if action == sqlite3.SQLITE_INSERT and table == 'execution_records' and observed:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_trace_callback(trace)
        connection.set_authorizer(authorize)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3, 'connect', interrupted_connection)
        with pytest.raises((sqlite3.DatabaseError, module.ExecutionError), match='not authorized'):
            prepare(store)
    assert observed, 'Fault must occur after a real schema write'
    assert snapshot(path) == before
    prepare(store)
    assert stored_records(path) == initial_records()


def test_unknown_source_format_has_an_actionable_refusal(tmp_path):
    module, store, path, _ = installation(tmp_path, source=True)
    with CONNECT(path) as db:
        db.execute('PRAGMA user_version=9')
    before = snapshot(path)
    with pytest.raises(module.ExecutionError) as error:
        prepare(store)
    assert str(error.value)
    assert snapshot(path) == before
