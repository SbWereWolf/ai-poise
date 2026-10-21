"""T048 ordinary attached statement failure/retry; no host-crash atomicity claim."""
import json
import sqlite3
import pytest

from process_checks import (assert_same, create, document, edit, fixture, in_process,
                            invoke, published, state)
from process_storage_checks import (assert_retained, connect_with, editor_rows,
                                    finalization_interrupt, install_populated_v1,
                                    modes, retained_rows)
from store_checks import snapshot


@pytest.mark.parametrize('mode', ['delete', 'wal'])
def test_published_candidate_finalization_rolls_back_and_retries_once(tmp_path, monkeypatch, mode):
    config, core, editor = install_populated_v1(tmp_path, mode)
    original = retained_rows(core)
    first = published(invoke(config, create(request='base-1', revision=7)), revision=8)
    before_core, before_editor = snapshot(core), editor_rows(editor)
    packet = edit(first, fixture('edit_changes'), request='edit-finalize')
    observations = []
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect_with(finalization_interrupt(config, observations)))
        code, pending = in_process(config, packet)
    assert observations, 'Fault must follow a real finalization write after candidate publication'
    assert_same(observations[0]['transaction_open'], True)
    assert observations[0]['rows_after_successful_write']
    assert code == 3 and pending['status'] == 'pending', pending
    assert isinstance(pending['intent_id'], str) and pending['intent_id'], pending
    assert_same(pending['phase'], 'process_publication')
    assert snapshot(core) == before_core
    assert_same(document(config), fixture('edited'))
    actual = editor_rows(editor)
    assert_same(actual['heads'], before_editor['heads'])
    assert_same([row for row in actual['operations'] if row[0] == 'base-1'], before_editor['operations'])
    reserved = [row for row in actual['operations'] if row[0] == 'edit-finalize']
    assert_same(len(reserved), 1)
    assert_same(reserved[0][-1], 'pending')
    assert_same(json.loads(reserved[0][4]), fixture('edited'))
    assert_same(modes(core, editor), [mode, mode])
    # Restore the test-owned statement condition, then retry this exact request
    # before any other application mutation, as required by proper use.
    code, completed = in_process(config, packet)
    assert code == 0 and completed['status'] == 'published', completed
    assert set(completed) == set(first), completed
    assert_same(completed['schema'], 'stride-execution/v1')
    assert_same(completed['goal_type'], 'writing')
    assert_same(completed['changed'], True)
    assert isinstance(completed['process_revision'], str) and completed['process_revision'], completed
    assert_same(completed['revision'], 9)
    assert_same(completed['replayed'], True)
    assert completed['previous_process_revision'] == first['process_revision']
    assert completed['process_revision'] != first['process_revision']
    assert_retained(core, original, 9, 9)
    finished = editor_rows(editor)
    assert_same(finished['heads'], [['writing', completed['process_revision']]])
    assert_same([[row[0], row[-1]] for row in finished['operations']],
                [['base-1', 'completed'], ['edit-finalize', 'completed']])
    after = state(config)
    code, replay = invoke(config, packet)
    assert code == 0
    assert_same(replay, completed)
    assert state(config) == after
    assert_same(document(config), fixture('edited'))
    assert_same(modes(core, editor), [mode, mode])
