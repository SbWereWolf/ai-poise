"""Current editable source selection, distinct from frozen Task contracts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.application.catalogue import CatalogueCommands
from poise.common import PoiseError
from poise.composition import goal_config_tools
from poise.infrastructure.catalogue import FileCatalogue
from poise.modules.tasks.planning import validate_planning
from tests.conftest import write_json
from tests.goal_config.helpers import settings, request
from tasks.test_planning_flexibility import planning_catalogue, client, ready, POLICY
from tasks.test_newborn_lifecycle import edit, task_action


def workflow_sources(tmp_path):
    path, _ = settings(tmp_path, definitions={
        'writing': 'workflows/writing.json', 'derived': 'workflows/derived.json',
    })
    raw = json.loads(path.read_text())
    raw['templates']['writing-v1'] = {'path': 'templates/writing.json', 'operator_note': 'Keep me'}
    write_json(path, raw)
    source = tmp_path / 'templates/writing.json'
    graph = json.loads(source.read_text())
    graph['stages'][0]['instruction'] = 'Current editable starter instruction'
    write_json(source, graph)
    return path, source


@pytest.mark.parametrize('goal', ['writing', 'derived'])
def test_public_editor_uses_current_path_only_source_and_id_selection(tmp_path, goal):
    path, _ = workflow_sources(tmp_path)
    before = path.read_bytes()
    result = goal_config_tools(path).apply_batch(request(
        'create', goal, None, [], selection={'id': 'writing-v1'},
    ))
    current = json.loads(Path(result['config_path']).read_text())
    assert current['stages'][0]['instruction'] == 'Current editable starter instruction'
    assert current['goal_type'] == goal
    assert path.read_bytes() == before


def test_catalogue_install_selects_current_workflow_by_id(tmp_path):
    editor, _ = workflow_sources(tmp_path)
    catalogue = write_json(tmp_path / 'catalogue.json', {
        'schema': 'workflow-catalogue-1', 'editor': editor.name,
        'max_items': 10, 'task_templates': {}, 'operator_note': {'preserve': True},
    })
    owner = FileCatalogue(catalogue)
    before = catalogue.read_bytes()
    assert owner.workflow_selection('writing') == {'id': 'writing-v1'}
    result = CatalogueCommands(owner, goal_config_tools(editor), 10).install([{
        'goal_type': 'writing', 'request_id': 'install-current', 'mode': 'create',
        'expected_revision': None, 'template': {'id': 'writing-v1'}, 'changes': [],
    }])
    graph = json.loads(Path(result['results'][0]['config_path']).read_text())
    assert graph['stages'][0]['instruction'] == 'Current editable starter instruction'
    assert catalogue.read_bytes() == before


def test_public_task_materializes_current_starter_and_continues_without_source(project):
    source = planning_catalogue(project)
    catalogue = Path(project['cfg']['task_planning']['catalogue'])
    raw = json.loads(catalogue.read_text())
    raw['task_templates']['dev-test'] = {
        'path': 'templates/development.json', 'goal_type': 'development',
        'operator_note': 'Preserve this option',
    }
    write_json(catalogue, raw)
    template = json.loads(source.read_text())
    template['task']['definition_of_done'] = ['Current editable starter definition of done']
    write_json(source, template)
    before = catalogue.read_bytes()
    api = client(project)
    out = task_action(api, action='create', task_id='CURRENT', sprint_id=None,
                     request_id='current-starter', goal_type='development',
                     patch={'goal': 'Maintain current Task starter selection'})
    assert out['draft']['definition_of_done'] == ['Current editable starter definition of done']
    assert out['draft']['planning']['template'] == {'id': 'dev-test'}
    source.unlink()
    out = edit(api, 'CURRENT', out['revision'], {'goal_type': 'development'}, 'keep-frozen')
    assert ready(api, 'CURRENT', out['revision'])['status'] == 'available'
    with api.runtime.store.unit_of_work() as uow:
        saved = uow.tasks.load('CURRENT')
        assert saved.state.task_id == 'CURRENT'
        assert saved.stages[0].stage_id == 'tests'
        assert saved.state.status.value == 'available'
    record = api.runtime.task_queries.record('CURRENT')
    assert record['contract']['definition_of_done'] == ['Current editable starter definition of done']
    assert catalogue.read_bytes() == before


@pytest.mark.parametrize('selection', [
    None,
    {'id': 'starter'},
    {'id': 'starter', 'version': ''},
    {'id': 'starter', 'digest': 'Historical free text'},
    {'id': 'starter', 'version': 'historic', 'digest': 'Saved audit text'},
])
def test_planning_keeps_optional_historical_audit_without_source_lookup(selection):
    value = {'schema': 'task-planning-1', 'template': deepcopy(selection),
             'restart_revision_policy': deepcopy(POLICY)}
    before = deepcopy(value)
    assert validate_planning(value) == before
    assert value == before


@pytest.mark.parametrize('selection', [{'id': ''}, {'id': '  '}, {'id': 'starter', 'unexpected': 'field'}])
def test_task_planning_requires_identity_and_rejects_unknown_fields(selection):
    with pytest.raises(PoiseError):
        validate_planning({'schema': 'task-planning-1', 'template': selection,
                           'restart_revision_policy': deepcopy(POLICY)})


def test_task_registry_goal_identity_still_matches_its_source(project):
    planning_catalogue(project)
    catalogue = Path(project['cfg']['task_planning']['catalogue'])
    raw = json.loads(catalogue.read_text())
    raw['task_templates']['dev-test']['goal_type'] = 'review'
    write_json(catalogue, raw)
    owner = FileCatalogue(catalogue)
    with pytest.raises(PoiseError, match='identity mismatch'):
        owner.task_blueprint({'id': 'dev-test'})


def test_malformed_current_source_is_explicit_and_does_not_publish(tmp_path):
    path, source = workflow_sources(tmp_path)
    write_json(source, {})
    with pytest.raises(PoiseError, match='goal_type|content_contract|worktree_required|stages'):
        goal_config_tools(path).apply_batch(request(
            'create', 'writing', None, [], selection={'id': 'writing-v1'},
        ))
    assert not (tmp_path / 'workflows/writing.json').exists()


def test_changed_demo_producer_publishes_first_packet_through_real_cli(project, monkeypatch):
    import importlib.util
    import subprocess
    import sys

    repository = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repository / 'examples'))
    spec = importlib.util.spec_from_file_location('current_starter_demo', repository / 'examples/goal_config_demo.py')
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    home = project['config_path'].parent
    assert project['config_path'] == home / 'project.json'
    source = home / 'config/workflows/development.json'
    before = source.read_bytes()
    monkeypatch.setattr(demo, 'create', lambda directory: home)
    original_run = subprocess.run
    observed = []

    class FirstPacketPublished(Exception):
        pass

    def first_command(command, *args, **kwargs):
        assert command[:4] == [sys.executable, '-m', 'poise', 'goal-config']
        packet = json.loads(kwargs['input'])
        assert packet['template'] == {'id': 'development-v1'}
        completed = original_run(command, *args, **kwargs)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        observed.append(completed)
        raise FirstPacketPublished

    monkeypatch.setattr(demo.subprocess, 'run', first_command)
    with pytest.raises(FirstPacketPublished):
        demo.run(home)
    assert len(observed) == 1
    created = json.loads((home / 'config/workflows/generated.json').read_text())
    assert created['goal_type'] == 'development'
    assert created['stages'][0]['instruction'] == json.loads(before)['stages'][0]['instruction']
    assert source.read_bytes() == before


def test_current_json_example_uses_the_public_selection_schema():
    from poise.application.goal_config import validate_batch_request

    repository = Path(__file__).resolve().parents[2]
    packet = json.loads((repository / 'examples/goal_config_create.json').read_text())
    assert packet['template'] == {'id': 'documentation-v1'}
    validate_batch_request(packet, 2000)
