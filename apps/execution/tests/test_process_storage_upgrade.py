"""T048 literal populated version1→2 transition through real existing app entry."""
import sqlite3
import pytest

from process_checks import (applied, assert_same, create, fixture, frozen, in_process,
                            invoke, published, rejected, setup, state, store)
from process_storage_checks import (CONNECT, assert_reopens_with_foreign_keys, assert_retained, assert_v2_shape,
                                    connect_with, install_populated_v1, legacy_values,
                                    migration_interrupt, modes, retained_rows,
                                    schema_observation)
from store_checks import snapshot


@pytest.mark.parametrize('mode', ['delete', 'wal'])
def test_populated_v1_upgrade_retains_values_and_supports_optional_workspace(tmp_path, mode):
    config, core, editor = install_populated_v1(tmp_path, mode)
    before, schema = retained_rows(core), schema_observation(core)
    packet = create(request='upgrade-1', revision=7)
    first = published(invoke(config, packet), revision=8)
    assert_retained(core, before, 8, 8)
    assert_v2_shape(core, schema)
    assert_reopens_with_foreign_keys(config)
    assert_same(modes(core, editor), [mode, mode])
    actual = store(config).read_records('0042')
    expected = [row for row in legacy_values()['initial_records'] + legacy_values()['records']
                if row['task_id'] == '0042']
    assert_same(actual, expected)
    new_packet = setup(first, task='0044', request='optional-workspace')
    applied(invoke(config, new_packet), '0044')
    frozen(config, '0044', fixture('definition'), new_packet['input']['operations'][0]['contract'])
    assert_retained(core, before, 9, 9, new_executions=1, new_records=2)
    with CONNECT(core) as db:
        assert_same(db.execute('SELECT workspace_id FROM executions WHERE task_id=?', ('0044',)).fetchone()[0], None)
    after = state(config)
    code, replay = invoke(config, packet)
    expected_response = dict(first, replayed=True)
    assert code == 0
    assert_same(replay, expected_response)
    assert state(config) == after
    assert_v2_shape(core, schema)
    assert_reopens_with_foreign_keys(config)
    assert_same(modes(core, editor), [mode, mode])


@pytest.mark.parametrize('checkpoint', ['ddl', 'data'])
def test_real_upgrade_interruption_rolls_back_then_exact_request_can_retry(tmp_path, monkeypatch, checkpoint):
    config, core, editor = install_populated_v1(tmp_path)
    before = state(config)
    rows, schema = retained_rows(core), schema_observation(core)
    observations = []
    packet = create(request='upgrade-retry', revision=7)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect_with(migration_interrupt(checkpoint, observations)))
        rejected(in_process(config, packet), 'test-owned migration interruption', 'interrupted')
    assert observations, 'Interruption must follow actual SQLite schema/data effects'
    assert_same(observations[0]['transaction_open'], True)
    if checkpoint == 'data':
        assert_same(observations[0]['rows_copied'], 2)
    assert state(config) == before
    with CONNECT(core) as db:
        assert_same(db.execute('PRAGMA user_version').fetchone()[0], 1)
        assert_same([list(row) for row in db.execute('PRAGMA foreign_key_check')], [])
    published(invoke(config, packet), revision=8)
    assert_retained(core, rows, 8, 8)
    assert_v2_shape(core, schema)
    assert_reopens_with_foreign_keys(config)
    # A real fresh connection reopens and retains the already upgraded state.
    observed = snapshot(core)
    store(config).read_records('0042')
    assert snapshot(core) == observed
