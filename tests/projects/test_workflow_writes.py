"""Selected workflow writes and candidate purity; synthetic project data only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from conftest import write_json
from poise.common import digest, load_config_document
from poise.composition import project_config_tools
from poise.infrastructure.project_config import FileProjectConfigUpdate
from poise.infrastructure.projects import ProjectSettings
from poise.modules.foundation.errors import PoiseError, VersionConflict, DomainError


def external_case(project, tmp_path):
    path = project['config_path']
    config = deepcopy(project['cfg'])
    config.pop('workflows')
    workflow = deepcopy(project['workflow'])
    selected = tmp_path / 'working-data/workflows/development.json'
    write_json(selected, workflow)
    config['workflows'] = {'development': str(selected)}
    write_json(path, config)
    unrelated = write_json(tmp_path / 'examples/workflows/development.json', workflow)
    settings = ProjectSettings(config['runtime_resources']['project_setup'])
    return path, config, workflow, selected, unrelated, settings


def packet(path, config, workflow, *, request_id='selected-workflow-update'):
    return {
        'schema': 'project-config-update-1',
        'request_id': request_id,
        'config_path': str(path),
        'expected_revision': digest({'config': config, 'workflows': {'development': workflow}}),
        'manifest_edits': [],
        'workflow_updates': [{
            'goal_type': 'development',
            'expected_revision': digest(workflow),
            'changes': [{'op': 'patch_stage', 'id': 'tests',
                         'set': {'instruction': 'Use the selected project-owned workflow.'}}],
        }],
        'state_relocation': None,
        'probe_repository': False,
        'receipt_path': 'operations/selected-workflow-update.json',
    }


def test_candidate_overlay_does_not_write_absolute_live_workflow(project, tmp_path):
    path, config, workflow, selected, unrelated, settings = external_case(project, tmp_path)
    before = selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()
    candidate = deepcopy(workflow)
    candidate['stages'][0]['instruction'] = 'Candidate inspection is read only.'
    owner = FileProjectConfigUpdate(settings)

    owner._validate_candidate(path.parent, config, {'development': candidate})

    assert (selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()) == before
    assert sorted(p.name for p in selected.parent.iterdir()) == ['development.json']


def test_invalid_candidate_does_not_modify_selected_file(project, tmp_path):
    path, config, workflow, selected, unrelated, settings = external_case(project, tmp_path)
    before = selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()
    candidate = deepcopy(workflow)
    del candidate['goal_type']

    with pytest.raises(DomainError, match='goal_type'):
        FileProjectConfigUpdate(settings)._validate_candidate(path.parent, config, {'development': candidate})

    assert (selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()) == before


def test_external_workflow_public_write_round_trip_and_exact_retry(project, tmp_path):
    path, config, workflow, selected, unrelated, settings = external_case(project, tmp_path)
    example_before = unrelated.read_bytes()
    request = packet(path, config, workflow)
    api = project_config_tools(settings.path)

    first = api.apply(request)
    after = selected.read_bytes()
    second = api.apply(request)

    _, _, observed = load_config_document(path, json.loads(path.read_text()))
    expected = deepcopy(workflow)
    expected['stages'][0]['instruction'] = 'Use the selected project-owned workflow.'
    assert observed == {'development': expected}
    assert json.loads(after) == expected
    assert selected.read_bytes() == after
    assert unrelated.read_bytes() == example_before
    assert first['status'] == 'updated'
    assert second['replayed'] is True


def test_stale_external_workflow_write_preserves_current_file(project, tmp_path):
    path, config, workflow, selected, unrelated, settings = external_case(project, tmp_path)
    request = packet(path, config, workflow)
    later = deepcopy(workflow)
    later['stages'][0]['instruction'] = 'Later project edit must survive.'
    write_json(selected, later)
    before = selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()

    with pytest.raises(VersionConflict, match='revision|Revision'):
        project_config_tools(settings.path).apply(request)

    assert (selected.read_bytes(), unrelated.read_bytes(), path.read_bytes()) == before


def test_reduced_config_graph_boundary_does_not_require_harness_services(project, tmp_path):
    path, config, workflow, selected, unrelated, settings = external_case(project, tmp_path)
    config['runtime_services'].pop('handoff', None)
    config['runtime_services'].pop('transfer', None)
    write_json(path, config)
    from poise.infrastructure import workflow_files

    observed = workflow_files.read_project_workflows(path)

    assert observed == {'development': workflow}
    assert 'handoff' not in json.loads(path.read_text())['runtime_services']
    assert 'transfer' not in json.loads(path.read_text())['runtime_services']


def test_current_workflow_snapshot_uses_external_selected_graph(project, tmp_path):
    from poise.composition import project_tools
    from tests.projects.helpers import setup_case
    settings_path, _, request = setup_case(project)
    result = project_tools(settings_path).apply(request)
    path = Path(result['config_path'])
    config = json.loads(path.read_text())
    own = deepcopy(project['workflow'])
    own['stages'][0]['instruction'] = 'Read these current project-owned bytes.'
    selected = write_json(tmp_path / 'selected-data/workflows/development.json', own)
    config['workflows']['development'] = str(selected)
    write_json(path, config)
    expected_bytes = selected.read_bytes()
    owner = FileProjectConfigUpdate(ProjectSettings(settings_path))

    binding = owner.stage_criteria_binding(path)
    snapshot = owner.stage_criteria_snapshot(path)

    assert binding['targets'] == {'development': str(selected)}
    assert snapshot['bytes'] == {'development': expected_bytes}
    assert selected.read_bytes() == expected_bytes
