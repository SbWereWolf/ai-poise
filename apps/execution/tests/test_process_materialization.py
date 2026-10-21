"""T048 selected process snapshots, all-or-nothing setup and replay-first."""
from copy import deepcopy
import json
import pytest

from process_checks import (applied, assert_same, create, current, edit, fixture, frozen,
                            installation, invoke, published, rejected, setup, state, write_json)


def test_existing_task_stays_frozen_after_edit_and_template_change(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create(template=True)), revision=1)
    old_packet = setup(first)
    old = applied(invoke(config, old_packet))
    old_records = frozen(config, '0042', fixture('definition'), old_packet['input']['operations'][0]['contract'])
    second = published(invoke(config, edit(first, fixture('edit_changes'), revision=old['revision'])),
                       revision=old['revision'] + 1, previous=first['process_revision'])
    new_packet = setup(second, task='0043', request='setup-2')
    new = applied(invoke(config, new_packet), '0043')
    frozen(config, '0043', fixture('edited'), new_packet['input']['operations'][0]['contract'])
    assert_same(frozen(config, '0042', fixture('definition'), old_packet['input']['operations'][0]['contract']), old_records)
    write_json(tmp_path / 'state/templates/starter.json', fixture('alternative'))
    published(invoke(config, create(goal='notes', request='notes-1', revision=new['revision'], template=True)),
              revision=new['revision'] + 1, goal='notes')
    assert_same(json.loads(current(config).read_text()), fixture('edited'))
    assert_same(frozen(config, '0042', fixture('definition'), old_packet['input']['operations'][0]['contract']), old_records)


@pytest.mark.parametrize('case', ['unknown', 'unpublished', 'revision'])
def test_unresolvable_selected_process_leaves_all_setup_state_unchanged(tmp_path, case):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    packet = setup(first)
    ref = packet['input']['operations'][0]['contract']['process_ref']
    if case == 'unknown':
        ref['goal_type'] = 'unregistered'
    elif case == 'unpublished':
        ref['goal_type'] = 'notes'
    else:
        ref['revision'] = '0' * 64
    before = state(config)
    rejected(invoke(config, packet), 'process', 'процесс', 'revision', 'notes', 'unregistered')
    assert state(config) == before


def test_valid_first_invalid_second_setup_is_one_atomic_batch(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    packet = setup(first)
    second = deepcopy(setup(first, task='0043')['input']['operations'][0])
    second['id'] = 'setup-second'
    second['contract']['process_ref']['revision'] = '0' * 64
    packet['input']['operations'].append(second)
    before = state(config)
    rejected(invoke(config, packet), 'process', 'процесс', 'revision')
    assert state(config) == before


@pytest.mark.parametrize('source_change', ['edited', 'missing'])
def test_completed_setup_replay_uses_saved_snapshot_before_source_resolution(tmp_path, source_change):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    packet = setup(first)
    result = applied(invoke(config, packet))
    original = frozen(config, '0042', fixture('definition'), packet['input']['operations'][0]['contract'])
    if source_change == 'edited':
        published(invoke(config, edit(first, fixture('edit_changes'), revision=result['revision'])),
                  revision=result['revision'] + 1, previous=first['process_revision'])
    else:
        current(config).unlink()
    before = state(config)
    replay = applied(invoke(config, packet))
    expected = deepcopy(result)
    expected['replayed'] = True
    assert_same(replay, expected)
    assert state(config) == before
    assert_same(frozen(config, '0042', fixture('definition'), packet['input']['operations'][0]['contract']), original)
