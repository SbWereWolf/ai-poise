"""Configuration loading and composition without retired filesystem services."""
from copy import deepcopy
import json

import pytest

from conftest import write_json
from poise.common import load_config_document
from poise.modules.foundation.errors import PoiseError


@pytest.mark.parametrize('removed', [('handoff',), ('transfer',), ('handoff', 'transfer')])
def test_retired_service_sections_are_not_required(project, removed):
    config = deepcopy(project['cfg'])
    for key in removed:
        config['runtime_services'].pop(key, None)

    _, observed, graphs = load_config_document(project['config_path'], config)

    assert observed == config
    assert graphs == {'development': project['workflow']}
    assert all(key not in observed['runtime_services'] for key in removed)


def test_retired_unknown_settings_are_preserved_without_validation(project):
    config = deepcopy(project['cfg'])
    config['runtime_services']['handoff'] = 'unused operator value'
    config['runtime_services']['transfer'] = {'unused': True}

    _, observed, _ = load_config_document(project['config_path'], config)

    assert observed == config


def test_active_output_settings_are_still_required(project):
    config = deepcopy(project['cfg'])
    del config['runtime_services']['output']

    with pytest.raises(PoiseError, match='output'):
        load_config_document(project['config_path'], config)


def test_runtime_composes_without_retired_filesystem_services(project):
    from conftest import DeterministicClock
    from poise.runtime import Poise
    config = deepcopy(project['cfg'])
    config['runtime_services'].pop('handoff', None)
    config['runtime_services'].pop('transfer', None)
    write_json(project['config_path'], config)

    runtime = Poise(project['config_path'], 'retired-services-test', DeterministicClock())

    assert runtime.cfg == config
    assert json.loads(project['config_path'].read_text()) == config
