"""Actual SQLite transactions, deterministic interruption and read snapshots."""
from copy import deepcopy
import json
import sqlite3
import pytest
from store_checks import (APP, CONNECT, ConnectionProxy, fixture, initial_records,
                          installation, prepare, revision, snapshot,
                          stored_records, write)


def prepared(root):
    result = installation(root, source=True)
    prepare(result[1])
    write(result[1])
    return result


def test_later_conflict_rolls_back_an_earlier_valid_record(tmp_path):
    module, store, path, _ = prepared(tmp_path)
    valid = deepcopy(fixture()['concurrent_records'][1])
    conflict = deepcopy(next(x for x in fixture()['records'] if x['kind'] == 'evidence'))
    conflict['data']['passed'] = not conflict['data']['passed']
    before = snapshot(path)
    with pytest.raises(module.ExecutionError):
        write(store, [valid, conflict], request='failed-batch', revision=8)
    assert snapshot(path) == before
    write(store, [valid], request='failed-batch', revision=8)
    assert stored_records(path)[-1] == valid
    assert revision(path) == 9


def test_attempt_and_receipt_changes_share_one_transaction(tmp_path):
    module, store, path, _ = prepared(tmp_path)
    update = deepcopy(fixture()['concurrent_records'][0])
    receipt = deepcopy(next(x for x in fixture()['records'] if x['kind'] == 'evidence'))
    receipt['id'] = receipt['data']['id'] = 'receipt-after-update'
    invalid = deepcopy(receipt)
    invalid['id'] = 'invalid-owner'
    invalid['task_id'] = '0099'
    before = snapshot(path)
    with pytest.raises(module.ExecutionError):
        write(store, [update, receipt, invalid], request='update-batch', revision=8)
    assert snapshot(path) == before
    assert store.read_attempt('0042', 'attempt-0042')['revision'] == 3
    write(store, [update, receipt], request='update-batch', revision=8)
    assert store.read_attempt('0042', 'attempt-0042') == update
    assert stored_records(path)[-2:] == [update, receipt]


def test_actual_second_insert_failure_rolls_back_written_prefix(tmp_path, monkeypatch):
    module, store, path, _ = prepared(tmp_path)
    first = deepcopy(fixture()['concurrent_records'][1])
    first['id'] = first['data']['event_id'] = 'event-before-failure'
    second = deepcopy(first)
    second['id'] = second['data']['event_id'] = 'forced-failure'
    second['data']['ordinal'] = 5
    before = snapshot(path)
    statements = []

    def interrupted_connection(*args, **kwargs):
        connection = CONNECT(*args, **kwargs)
        connection.executescript((APP / 'fixtures/store-interruption.sql').read_text())
        connection.set_trace_callback(statements.append)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3, 'connect', interrupted_connection)
        with pytest.raises((sqlite3.DatabaseError, module.ExecutionError), match='test-owned batch failure'):
            write(store, [first, second], request='interrupted-write', revision=8)
    assert any('event-before-failure' in sql for sql in statements), 'The successful prefix must reach actual SQLite'
    assert snapshot(path) == before
    write(store, [first, second], request='interrupted-write', revision=8)
    assert stored_records(path)[-2:] == [first, second]


@pytest.mark.parametrize('case', ['stale_revision', 'request_conflict'])
def test_revision_or_request_conflict_preserves_all_rows(tmp_path, case):
    module, store, path, _ = prepared(tmp_path)
    before = snapshot(path)
    records = fixture()['concurrent_records']
    with pytest.raises(module.ExecutionError):
        write(store, records, request='new-request' if case == 'stale_revision' else 'records-1', revision=7)
    assert snapshot(path) == before


def test_read_observes_one_snapshot_across_concurrent_commit(tmp_path, monkeypatch):
    module, store, path, _ = prepared(tmp_path)
    with CONNECT(path) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    old = [row for row in initial_records() + fixture()['records'] if row['task_id'] == '0042']
    added = fixture()['concurrent_records']
    committed = []

    def after_first_fetch():
        if committed:
            return
        with CONNECT(path) as writer:
            writer.execute('BEGIN IMMEDIATE')
            for row in added:
                writer.execute('INSERT INTO execution_records(task_id,kind,record_id,record_revision,generation,data_json) VALUES(?,?,?,?,?,?)',
                               (row['task_id'], row['kind'], row['id'], row['revision'], row['generation'], json.dumps(row['data'], ensure_ascii=False)))
            writer.execute("UPDATE meta SET value=? WHERE key='revision'", (9,))
        committed.append(True)

    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3, 'connect', lambda *a, **kw: ConnectionProxy(CONNECT(*a, **kw), fetched=after_first_fetch))
        actual = store.read_records('0042')
    assert committed == [True], 'Writer must commit at the observed real read boundary'
    assert actual in (old, old + added), 'A read batch must not mix old and new records'
    assert stored_records(path, '0042') == old + added
