"""T048 persisted editor, exact request replay and ordinary publication recovery."""
from copy import deepcopy
import json
import os
import sqlite3

from process_checks import (applied, assert_same, create, current, document, edit, fixture,
                            in_process, installation, invoke, published, rejected,
                            state, status, write_json)


def test_status_noop_and_completed_replay_after_later_edit(tmp_path):
    config = installation(tmp_path)
    packet = create()
    first = published(invoke(config, packet), revision=1)
    no_op = published(invoke(config, edit(first, [], request='noop')), revision=2,
                      previous=first['process_revision'], changed=False)
    assert no_op['process_revision'] == first['process_revision']
    second = published(invoke(config, edit(no_op, fixture('edit_changes'))), revision=3,
                       previous=first['process_revision'])
    before = state(config)
    code, replay = invoke(config, packet)
    expected = deepcopy(first)
    expected['replayed'] = True
    assert code == 0
    assert_same(replay, expected)
    assert state(config) == before
    assert_same(document(config), fixture('edited'))
    code, observed = invoke(config, status())
    assert code == 0
    assert_same(observed, {'schema': 'stride-execution/v1', 'status': 'read_only',
                          'revision': 3, 'goal_type': 'writing',
                          'managed_revision': second['process_revision'],
                          'live_revision': second['process_revision'], 'pending_request': None})


def test_stale_revision_reused_request_and_existing_creation_leave_state_intact(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    before = state(config)
    stale = edit(first, fixture('edit_changes'))
    stale['input']['expected_process_revision'] = '0' * 64
    rejected(invoke(config, stale), 'revision', 'верси')
    reused = create(changes=fixture('edit_changes'))
    rejected(invoke(config, reused), 'request', 'запрос')
    rejected(invoke(config, create(request='duplicate', revision=1)), 'exist', 'существ')
    assert state(config) == before


def test_real_replace_failure_is_pending_and_exact_immediate_retry_completes(tmp_path, monkeypatch):
    config = installation(tmp_path)
    target = current(config)
    packet = create()
    original = os.replace
    hits = []

    def fail_selected(source, destination, *args, **kwargs):
        if os.fspath(destination) == str(target):
            hits.append(str(destination))
            raise OSError('test-owned full disk before replacement')
        return original(source, destination, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(os, 'replace', fail_selected)
        code, pending = in_process(config, packet)
    assert hits == [str(target)], hits
    assert code == 3 and pending['status'] == 'pending', pending
    assert pending['phase'] == 'process_publication' and pending['intent_id'], pending
    assert not target.exists()
    code, completed = in_process(config, packet)
    assert code == 0 and completed['status'] == 'published' and completed['replayed'] is True, completed
    assert completed['revision'] == 1
    assert_same(document(config), fixture('definition'))
    before = state(config)
    assert_same(in_process(config, packet)[1], completed)
    assert state(config) == before
    with sqlite3.connect(tmp_path / 'state/process-editor.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM config_operations').fetchone()[0] == 1


def test_explicit_reconcile_adopts_valid_separate_publication_without_rewriting_file(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    alternative_config = installation(tmp_path / 'other')
    later = published(invoke(alternative_config, create(definition=fixture('edited'))), revision=1)
    write_json(current(config), fixture('edited'))
    raw = current(config).read_bytes()
    code, observed = invoke(config, status())
    assert code == 0
    assert observed['managed_revision'] == first['process_revision']
    assert observed['live_revision'] == later['process_revision']
    packet = {'schema': 'stride-execution/v1', 'operation': 'process', 'input': {
        'action': 'reconcile', 'request_id': 'adopt', 'expected_revision': 1, 'goal_type': 'writing',
        'expected_managed_revision': first['process_revision'],
        'expected_live_revision': later['process_revision'], 'authorization_id': 'accepted-publication',
        'reason': 'Принята опубликованная версия процесса.'}}
    code, result = invoke(config, packet)
    assert code == 0 and result['status'] == 'published' and result['revision'] == 2, result
    assert result['process_revision'] == later['process_revision']
    assert current(config).read_bytes() == raw
    assert invoke(config, status())[1]['managed_revision'] == later['process_revision']
