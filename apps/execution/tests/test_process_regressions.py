"""Confirmed CODE-F01/F02 regression cases through the real standalone CLI."""
from copy import deepcopy
import json
import pytest

from process_checks import (APP, assert_same, create, current, edit, fixture, installation,
                            invoke, published, rejected, state, status, write_json)


CASES = json.loads((APP / 'fixtures/process-regressions.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['case'] + '-' + case['route'])
def test_nested_reference_types_return_structured_rejection_without_effects(tmp_path, case):
    config = installation(tmp_path)
    published(invoke(config, create()), revision=1)
    packet = deepcopy(case['input'])
    if case['route'] == 'current':
        write_json(current(config), case['document'])
    elif case['route'] == 'template':
        write_json(config.parent / 'state/templates/starter.json', case['document'])
    before = state(config)
    # invoke asserts real CLI exit/output framing, empty stderr and schema.
    rejected(invoke(config, packet), case['field'])
    assert_same(state(config), before)


@pytest.mark.parametrize('action', ['status', 'edit', 'reconcile'])
def test_current_identity_refusal_preserves_state_and_saved_completed_replay(tmp_path, action):
    config = installation(tmp_path / 'selected')
    original_packet = create()
    first = published(invoke(config, original_packet), revision=1)
    # A separate valid installation supplies the other semantic revision without
    # advancing the selected owner's integer revision or using product helpers.
    notes_config = installation(tmp_path / 'notes')
    notes = published(invoke(notes_config, create(goal='notes', definition=fixture('alternative'))),
                      revision=1, goal='notes')
    write_json(current(config), fixture('alternative'))
    if action == 'status':
        packet = status()
    elif action == 'edit':
        packet = edit(dict(first, process_revision=notes['process_revision']), [], request='wrong-current')
    else:
        value = deepcopy(fixture('reconcile_input'))
        value.update(expected_revision=first['revision'],
                     expected_managed_revision=first['process_revision'],
                     expected_live_revision=notes['process_revision'])
        packet = {'schema': 'stride-execution/v1', 'operation': 'process', 'input': value}
    before = state(config)
    rejected(invoke(config, packet), 'goal_type', 'identity', 'идентич', 'имя')
    assert_same(state(config), before)
    code, replay = invoke(config, original_packet)
    assert code == 0
    assert_same(replay, dict(first, replayed=True))
    assert_same(state(config), before)
