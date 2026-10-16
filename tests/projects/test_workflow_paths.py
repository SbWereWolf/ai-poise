"""Workflow path contract: explicit project selection, independent file oracle."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conftest import write_json
from poise.common import load_config_document
from poise.modules.foundation.errors import PoiseError


def selected_config(project, workflow_path):
    config = deepcopy(project['cfg'])
    config.pop('workflows')
    config['workflows'] = {'development': str(workflow_path)}
    return config


@pytest.mark.parametrize('placement', [
    'relative_in_config',
    'relative_parent',
    'absolute_in_data',
    'absolute_external',
])
def test_selected_workflow_is_read_from_configured_file(project, tmp_path, monkeypatch, placement):
    config_path = project['config_path']
    config_dir = config_path.parent
    if placement == 'relative_in_config':
        value = 'task-graphs/development.json'
        target = config_dir / value
    elif placement == 'relative_parent':
        value = '../project-graphs/development.json'
        target = config_dir.parent / 'project-graphs/development.json'
    elif placement == 'absolute_in_data':
        target = config_dir / project['cfg']['paths']['state'] / 'workflows/development.json'
        value = str(target)
    else:
        target = tmp_path / 'independent-data/workflows/development.json'
        value = str(target)
    expected = deepcopy(project['workflow'])
    expected['stages'][0]['instruction'] = 'Read the explicitly selected workflow.'
    write_json(target, expected)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    config = selected_config(project, value)
    unrelated = tmp_path / 'unrelated-cwd'
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    _, observed_config, observed_workflows = load_config_document(config_path, config)

    assert observed_config['workflows'] == {'development': value}
    assert observed_workflows == {'development': expected}
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert 'processes' not in observed_config


def test_missing_selected_workflow_reports_selected_path_without_fallback(project, tmp_path):
    target = tmp_path / 'missing-workflows/development.json'
    config = selected_config(project, target)
    # The old fixture graph remains readable: falling back to it would hide the error.
    assert (project['config_path'].parent / 'config/workflows/development.json').is_file()

    with pytest.raises(PoiseError, match='development.json'):
        load_config_document(project['config_path'], config)

    assert not target.exists()
