"""Finite process API/configuration shape classes, without Cartesian expansion."""
from copy import deepcopy
import pytest

from process_checks import (create, edit, fixture, installation, invoke, published, status,
                            rejected, state, write_json)


def mutate(value, case):
    parent = value
    for part in case['path'][:-1]:
        parent = parent[part]
    key = case['path'][-1]
    if case['remove']:
        del parent[key]
    else:
        parent[key] = deepcopy(case['value'])


@pytest.mark.parametrize('case', fixture('api_input_cases'), ids=lambda case: case['name'])
def test_process_api_shape_refusal_preserves_complete_state(tmp_path, case):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    packet = create(goal='notes', definition=fixture('alternative'), request='candidate', revision=1)
    if case.get('base') == 'edit':
        packet = edit(first, [], request='candidate')
    elif case.get('base') == 'status':
        packet = status()
    elif case.get('base') == 'reconcile':
        packet = {'schema': 'stride-execution/v1', 'operation': 'process',
                  'input': fixture('reconcile_input')}
        packet['input']['expected_managed_revision'] = first['process_revision']
        packet['input']['expected_live_revision'] = first['process_revision']
    mutate(packet, case)
    before, raw_config = state(config), config.read_bytes()
    rejected(invoke(config, packet), *case['tokens'])
    assert state(config) == before
    assert config.read_bytes() == raw_config


@pytest.mark.parametrize('case', fixture('process_config_cases'), ids=lambda case: case['name'])
def test_known_process_configuration_shape_is_explicit(tmp_path, case):
    config = installation(tmp_path)
    published(invoke(config, create()), revision=1)
    values = fixture('config')
    mutate(values, case)
    write_json(config, values)
    before, raw_config = state(config), config.read_bytes()
    rejected(invoke(config, create(goal='notes', definition=fixture('alternative'),
                                   request='candidate', revision=1)), *case['tokens'])
    assert state(config) == before
    assert config.read_bytes() == raw_config
