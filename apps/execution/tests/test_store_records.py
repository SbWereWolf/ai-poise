"""Owned record semantics against literal data and independent SQL."""
from copy import deepcopy
import json
import subprocess
import pytest
from store_checks import (APP, CONNECT, fixture, initial_records, installation,
                          prepare, revision, snapshot, stored_records, subject, write)


def prepared(root):
    result = installation(root, source=True)
    prepare(result[1])
    return result


@pytest.mark.parametrize('source', [False, True], ids=['fresh', 'existing-owned-store'])
def test_real_setup_produces_a_readable_contract_snapshot(tmp_path, source):
    _, store, path, config = installation(tmp_path, source=source)
    packet = fixture()['setup_packet']
    packet['input']['expected_revision'] = 7 if source else 0
    proc = subprocess.run([str(APP / 'bin/execution'), '--config', str(config)],
                          input=json.dumps(packet)+'\n', text=True, capture_output=True, check=False)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    response = json.loads(proc.stdout)
    rows = store.read_records('0044')
    assert len(rows) == 1
    assert {key: rows[0][key] for key in ('id', 'kind', 'task_id', 'generation', 'revision')} == fixture()['setup_expected_record_identity']
    assert rows[0]['data']['contract'] == packet['input']['operations'][0]['contract']
    # This identity is supplied by the existing protocol, whose own tests prove its computation.
    assert rows[0]['data']['contract_revision'] == response['results'][0]['contract_revision']
    assert stored_records(path, '0044') == rows


def test_exact_records_and_json_types_survive_sql_and_reopen(tmp_path):
    _, store, path, config = prepared(tmp_path)
    records = fixture()['records']
    write(store)
    assert stored_records(path) == initial_records() + records
    module = subject()
    reopened = module.Store(module.load_config(config))
    for task in ('0042', '0043'):
        expected = [row for row in initial_records() + records if row['task_id'] == task]
        actual = reopened.read_records(task)
        assert actual == expected
    values = next(row['data']['value'] for row in reopened.read_records('0042') if row['kind'] == 'evidence')
    assert type(values['fraction']) is float
    assert type(values['integer']) is int
    assert type(values['bool']) is bool
    assert values['null'] is None
    assert values['array'][1] is False


def test_attempt_updates_retain_every_recorded_version(tmp_path):
    _, store, path, _ = prepared(tmp_path)
    write(store)
    expected = [x for x in fixture()['records'] if x['kind'] == 'attempt' and x['task_id'] == '0042']
    assert [x for x in stored_records(path, '0042') if x['kind'] == 'attempt'] == expected
    assert store.read_attempt('0042', 'attempt-0042') == expected[-1]
    assert [x['data']['state'] for x in expected] == ['reserved', 'running', 'completed']
    assert store.read_attempt('0043', 'attempt-0043')['data']['state'] == 'uncertain'


def test_evidence_and_restart_history_keep_source_order_and_decisions(tmp_path):
    _, store, path, _ = prepared(tmp_path)
    write(store)
    records = store.read_records('0042')
    history = [x for x in records if x['kind'] == 'history']
    assert [x['id'] for x in history] == ['z-proposal', 'b-approved', 'a-restarted']
    assert [x['data']['ordinal'] for x in history] == [1, 2, 3]
    assert history == [x for x in fixture()['records'] if x['kind'] == 'history']
    assert [x['data']['passed'] for x in records if x['kind'] == 'evidence'] == [False, True]
    assert stored_records(path, '0043') == [x for x in initial_records() + fixture()['records'] if x['task_id'] == '0043']


def test_request_replay_does_not_repeat_records_or_revision(tmp_path):
    _, store, path, _ = prepared(tmp_path)
    write(store)
    before = snapshot(path)
    write(store)
    assert snapshot(path) == before
    assert revision(path) == 8


def test_identical_record_identity_is_one_business_fact(tmp_path):
    _, store, path, _ = prepared(tmp_path)
    write(store)
    before = stored_records(path)
    write(store, request='records-2', revision=8)
    assert stored_records(path) == before


@pytest.mark.parametrize('kind', ['process_snapshot', 'contract_snapshot', 'attempt', 'evidence', 'history'])
def test_conflicting_record_identity_is_atomic(tmp_path, kind):
    module, store, path, _ = prepared(tmp_path)
    write(store)
    record = deepcopy(next(x for x in fixture()['records'] if x['kind'] == kind))
    record['data']['changed'] = 'different fact'
    before = snapshot(path)
    with pytest.raises(module.ExecutionError) as error:
        write(store, [record], request='conflict', revision=8)
    assert str(error.value)
    assert snapshot(path) == before


@pytest.mark.parametrize('change', ['unknown_task', 'wrong_association', 'revision_gap', 'unknown_kind', 'nonfinite'])
def test_normal_input_preconditions_leave_store_unchanged(tmp_path, change):
    module, store, path, _ = prepared(tmp_path)
    record = deepcopy(fixture()['records'][0])
    if change == 'unknown_task':
        record['task_id'] = '0099'
    elif change == 'wrong_association':
        record = deepcopy(next(x for x in fixture()['records'] if x['kind'] == 'evidence'))
        record['task_id'] = '0043'
    elif change == 'revision_gap':
        record = deepcopy(next(x for x in fixture()['records'] if x['kind'] == 'attempt'))
        record['revision'] = 3
    elif change == 'unknown_kind':
        record['kind'] = 'invented'
    else:
        record['data']['fraction'] = float('nan')
    before = snapshot(path)
    with pytest.raises(module.ExecutionError) as error:
        write(store, [record])
    assert str(error.value)
    assert snapshot(path) == before


def test_two_owned_configs_and_poisoned_neighbors_are_independent(tmp_path):
    _, first, first_db, config = prepared(tmp_path / 'first')
    _, second, second_db, _ = prepared(tmp_path / 'second')
    neighbor = tmp_path / 'requirements.json'
    neighbor.write_text('not configuration')
    foreign = tmp_path / 'requirements.sqlite'
    with CONNECT(foreign) as db:
        db.execute('CREATE TABLE statements(id TEXT PRIMARY KEY, text TEXT NOT NULL)')
        db.execute('INSERT INTO statements(id,text) VALUES(?,?)', ('REQ-1', 'Сохранить постановку'))
    foreign_before = snapshot(foreign)
    before = snapshot(second_db)
    write(first)
    assert snapshot(second_db) == before
    assert snapshot(foreign) == foreign_before
    assert second.read_records('0042') == [initial_records()[0]]
    assert first_db != second_db
    proc = subprocess.run([str(APP / 'bin/execution'), '--config', str(config)], input=json.dumps(fixture()['shell_query'])+'\n', text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    value = json.loads(proc.stdout)['results'][0]['value']
    assert value['task_id'] == '0042' and value['generation'] == 1
