"""Configured workflow placement and editable generator inputs, no provenance pins."""
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from poise.common import PoiseError, load_config, digest
from poise.composition import project_tools
from poise.infrastructure.projects import FileProjectSetup, ProjectSettings
from tests.conftest import write_json
from .helpers import setup_case


def external_request(project):
    settings, blueprint, request = setup_case(project)
    data = project['root'].parent / 'independent-data' / 'pilot'
    graph = data / 'workflows' / 'development.json'
    request['edits'].extend([
        {'path': ['paths', 'state'], 'value': str(data)},
        {'path': ['workflows', 'development'], 'value': str(graph)},
    ])
    return settings, blueprint, request, graph


def test_public_setup_requires_only_template_id_not_hash_version_or_receipt(project):
    settings, blueprint, request = setup_case(project)
    raw = json.loads(settings.read_text())
    raw['templates']['selected'] = {'path': 'config/project-blueprint.json'}
    raw.pop('receipt')
    write_json(settings, raw)
    blueprint['workflow_sources']['development'].pop('digest')
    blueprint.pop('version')
    write_json(project['root'] / 'config/project-blueprint.json', blueprint)
    request['template'] = {'id': 'selected'}

    result = project_tools(settings).apply(request)

    assert result['status'] == 'created'
    assert load_config(Path(result['config_path']))[1]['project'] == 'pilot'
    assert not (Path(result['config_path']).parent / 'setup-receipt.json').exists()


@pytest.mark.parametrize('relative', [False, True])
def test_generation_uses_exact_external_workflow_selection_from_unrelated_cwd(project, tmp_path, monkeypatch, relative):
    settings, _, request, graph = external_request(project)
    if relative:
        request['edits'][-1]['value'] = os.path.relpath(graph, project['root'] / request['destination'])
    unrelated = tmp_path / 'unrelated'; unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    result = FileProjectSetup(ProjectSettings(settings)).apply(request)

    manifest = Path(result['config_path'])
    _, config, documents = load_config(manifest)
    selected = Path(config['workflows']['development'])
    if not selected.is_absolute():
        selected = (manifest.parent / selected).resolve()
    assert selected == graph
    assert graph.is_file()
    assert documents['development']['goal_type'] == 'development'
    assert not (manifest.parent / 'config/workflows/development.json').exists()


def test_edited_blueprint_and_workflow_are_used_without_refreshing_pins(project):
    settings, blueprint, request = setup_case(project)
    blueprint['version'] = 'operator-current'
    blueprint['config']['limits']['output_chars'] = 3137
    write_json(project['root'] / 'config/project-blueprint.json', blueprint)
    source = project['root'] / 'config/workflows/development.json'
    document = json.loads(source.read_text())
    document['stages'][0]['instruction'] = 'Current operator instruction'
    write_json(source, document)

    result = FileProjectSetup(ProjectSettings(settings)).apply(request)

    assert load_config(Path(result['config_path']))[2]['development']['stages'][0]['instruction'] == 'Current operator instruction'
    assert load_config(Path(result['config_path']))[1]['limits']['output_chars'] == 3137


def test_repetition_preserves_current_operator_changes_without_setup_receipt(project):
    settings, _, request = setup_case(project)
    owner = FileProjectSetup(ProjectSettings(settings))
    first = owner.apply(request)
    manifest = Path(first['config_path'])
    saved = json.loads(manifest.read_text()); saved['git']['author_name'] = 'Current operator'
    write_json(manifest, saved)
    (manifest.parent / 'setup-receipt.json').unlink(missing_ok=True)
    graph = manifest.parent / saved['workflows']['development']
    changed = json.loads(graph.read_text())
    changed['stages'][0]['instruction'] = 'Current operator workflow'
    write_json(graph, changed)

    repeated = owner.apply(request)

    assert repeated['replayed'] is True
    assert json.loads(manifest.read_text())['git']['author_name'] == 'Current operator'
    _, current, graphs = load_config(manifest)
    assert graphs['development']['stages'][0]['instruction'] == 'Current operator workflow'
    assert repeated['revision'] == digest({'config': current, 'workflows': graphs})


def test_existing_external_file_is_never_replaced(project):
    settings, _, request, graph = external_request(project)
    graph.parent.mkdir(parents=True)
    graph.write_text('operator owned content\n')

    with pytest.raises(PoiseError, match='Workflow destination already exists'):
        FileProjectSetup(ProjectSettings(settings)).apply(request)

    assert graph.read_text() == 'operator owned content\n'
    assert not (project['root'] / request['destination']).exists()


@pytest.mark.parametrize('family', ['linux-reference', 'wsl-poise'])
def test_shipped_blueprint_creates_all_selected_workflows_under_data_root(tmp_path, family):
    repository = Path(__file__).resolve().parents[2]
    tools = tmp_path / 'explicit-tools'
    tools.mkdir()
    actual_settings = ProjectSettings(repository / 'config/project-setup.json')
    selected = actual_settings.raw['templates'][family]
    source_blueprint = json.loads((actual_settings.root / selected['path']).read_text())
    for goal, item in source_blueprint['workflow_sources'].items():
        actual_source = actual_settings.root / item['path']
        assert actual_source.is_file(), f'Missing configured source: {actual_source}'
        destination = tools / 'selected-sources' / (goal + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(actual_source, destination)
        item['path'] = str(destination.relative_to(tools))
    template_path = tools / 'blueprints' / (family + '.json')
    write_json(template_path, source_blueprint)
    registry = tools / 'project-registry.json'
    write_json(registry, {'schema': 'configured-project-registry-1', 'projects': {}})
    settings = json.loads((repository / 'config/project-setup.example.json').read_text())
    settings.update(root=str(tools), registry='project-registry.json', lock='setup.lock',
                    templates={family: {'path': 'blueprints/' + family + '.json'}})
    settings.pop('receipt', None)
    settings_path = write_json(tools / 'setup.json', settings)
    data = tmp_path / 'selected-data' / 'sample'
    config = deepcopy(source_blueprint['config'])
    roots = {'PROJECT_DATA_ROOT': str(data), 'PROJECT_CONFIG_ROOT': str(tools / 'projects/sample/config'),
             'POISE_SOURCE_ROOT': str(tools), 'REPOSITORY_ROOT': str(tmp_path / 'application'),
             'PROJECT_ID': 'sample', 'COMMIT_AUTHOR_NAME': 'Fixture',
             'COMMIT_AUTHOR_EMAIL': 'fixture@example.invalid'}
    def replace(value):
        if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list): return [replace(v) for v in value]
        if isinstance(value, str):
            for key, content in roots.items(): value = value.replace('${' + key + '}', content)
        return value
    config = replace(config)
    config['git']['push_required'] = False
    config['runtime_resources'] = {'project_setup': str(settings_path), 'hooks': []}
    edits = []
    for question in source_blueprint['questions']:
        value = config
        for key in question['path']: value = value[key]
        edits.append({'path': question['path'], 'value': value})
    edits.append({'path': ['runtime_resources', 'hooks'], 'value': []})
    result = project_tools(settings_path).apply({
        'schema': 'project-setup-1', 'request_id': 'generate-shipped',
        'destination': 'projects/sample/config', 'template': {'id': family},
        'edits': edits, 'probe_repository': False,
    })
    manifest = Path(result['config_path'])
    _, current, graphs = load_config(manifest)
    assert len(graphs) == 13
    assert current['paths']['state'] == str(data)
    assert all(Path(path).parent == data / 'workflows' for path in current['workflows'].values())
    assert all(Path(path).is_file() for path in current['workflows'].values())
    assert not (manifest.parent / 'config/workflows').exists()
    assert not (data / 'tasks/tasks.sqlite').exists()


def test_bootstrap_resources_uses_current_project_without_receipt_ancestry(project):
    from poise.composition import project_config_tools
    case = dict(project); case['cfg'] = deepcopy(project['cfg'])
    case['cfg'].pop('runtime_resources', None)
    settings, _, request = setup_case(case)
    created = FileProjectSetup(ProjectSettings(settings)).apply(request)
    manifest = Path(created['config_path'])
    (manifest.parent / 'setup-receipt.json').unlink(missing_ok=True)
    _, config, graphs = load_config(manifest)
    result = project_config_tools(settings).apply({
        'schema': 'project-runtime-resources-bootstrap-1', 'request_id': 'bind-current',
        'config_path': str(manifest),
        'expected_revision': digest({'config': config, 'workflows': graphs}),
        'runtime_resources': {'project_setup': str(settings), 'hooks': []},
        'receipt_path': 'operations/current-resources.json',
    })
    assert result['status'] == 'completed'
    assert load_config(manifest)[1]['runtime_resources']['project_setup'] == str(settings)


def test_foreign_unregistered_project_is_not_adopted(project):
    settings, blueprint, request = setup_case(project)
    destination = project['root'] / request['destination']
    destination.mkdir(parents=True)
    existing = deepcopy(blueprint['config']); existing['project'] = 'pilot'
    write_json(destination / 'project.json', existing)
    before = (destination / 'project.json').read_bytes()
    with pytest.raises(PoiseError, match='not registered'):
        FileProjectSetup(ProjectSettings(settings)).apply(request)
    assert (destination / 'project.json').read_bytes() == before


def test_named_pending_project_resumes_missing_graph_and_retires_marker(project):
    from poise.modules.projects.domain import ProjectBlueprint
    settings, blueprint, request, graph = external_request(project)
    destination = project['root'] / request['destination']
    destination.mkdir(parents=True)
    config = ProjectBlueprint.parse(blueprint).build(request['edits'], 1000)
    write_json(destination / 'project.json', config)
    marker = destination / '.project-setup-pending.json'
    write_json(marker, {'schema': 'project-setup-pending-1',
        'request_id': request['request_id'], 'project': 'pilot',
        'workflows': {'development': str(graph)}})
    result = FileProjectSetup(ProjectSettings(settings)).apply(request)
    assert result['replayed'] is True
    assert load_config(Path(result['config_path']))[2]['development']['goal_type'] == 'development'
    assert graph.is_file()
    assert not marker.exists()


def test_workflow_destination_cannot_replace_task_database(project):
    settings, _, request, graph = external_request(project)
    data = graph.parent.parent
    request['edits'][-1]['value'] = str(data / 'state.sqlite')
    with pytest.raises(PoiseError, match='overlap'):
        FileProjectSetup(ProjectSettings(settings)).apply(request)
    assert not data.exists()
    assert not (project['root'] / request['destination']).exists()


def test_setup_preserves_unknown_registry_options_and_entry_metadata(project):
    settings, _, request = setup_case(project)
    raw = json.loads(settings.read_text()); registry = project['root'] / raw['registry']
    content = json.loads(registry.read_text()); content['operator_note'] = {'preserve': 17}
    write_json(registry, content)
    owner = FileProjectSetup(ProjectSettings(settings))
    first = owner.apply(request)
    content = json.loads(registry.read_text())
    assert content['operator_note'] == {'preserve': 17}
    content['projects']['pilot']['operator_label'] = 'Keep this label'
    write_json(registry, content)
    assert owner.apply(request)['replayed'] is True
    assert json.loads(registry.read_text())['projects']['pilot']['operator_label'] == 'Keep this label'
    assert Path(first['config_path']).is_file()


@pytest.mark.parametrize('malformed', ['missing_project', 'invalid_workflow_path'])
def test_existing_project_schema_failure_is_explicit_and_preserves_bytes(project, malformed):
    settings, blueprint, request, graph = external_request(project)
    owner = FileProjectSetup(ProjectSettings(settings))
    if malformed == 'missing_project':
        result = owner.apply(request); manifest = Path(result['config_path'])
        config = json.loads(manifest.read_text()); config.pop('project')
    else:
        from poise.modules.projects.domain import ProjectBlueprint
        manifest = project['root'] / request['destination'] / 'project.json'
        manifest.parent.mkdir(parents=True)
        config = ProjectBlueprint.parse(blueprint).build(request['edits'], 1000)
        config['workflows']['development'] = 7
        write_json(manifest.parent / '.project-setup-pending.json', {
            'schema': 'project-setup-pending-1', 'request_id': request['request_id'],
            'project': 'pilot', 'workflows': {'development': str(graph)},
        })
    write_json(manifest, config); before = manifest.read_bytes()
    with pytest.raises(PoiseError):
        owner.apply(request)
    assert manifest.read_bytes() == before


@pytest.mark.parametrize('field', ['runtime', 'standalone_tasks', 'sprints', 'worktrees'])
def test_workflow_cannot_replace_configured_runtime_directory(project, field):
    settings, blueprint, request, graph = external_request(project)
    config = blueprint['config']; directory = Path(config['paths'][field])
    if not directory.is_absolute(): directory = graph.parent.parent / directory
    request['edits'][-1]['value'] = str(directory)
    with pytest.raises(PoiseError, match='overlap'):
        FileProjectSetup(ProjectSettings(settings)).apply(request)
    assert not directory.exists()
    assert not graph.parent.parent.exists()
    assert not (project['root'] / request['destination']).exists()


def test_registered_replay_rejects_malformed_edit_without_changing_files(project):
    settings, _, request = setup_case(project)
    commands = project_tools(settings)
    result = commands.apply(request)
    manifest = Path(result['config_path']); before = manifest.read_bytes()
    request['edits'].append(7)
    with pytest.raises(PoiseError):
        commands.apply(request)
    assert manifest.read_bytes() == before


def test_fresh_blueprint_missing_project_is_explicit_rejection(project):
    settings, blueprint, request = setup_case(project)
    blueprint['config'].pop('project')
    blueprint['questions'] = [q for q in blueprint['questions'] if q['path'] != ['project']]
    request['edits'] = [edit for edit in request['edits'] if edit['path'] != ['project']]
    source = project['root'] / 'config/project-blueprint.json'
    write_json(source, blueprint); before = source.read_bytes()
    with pytest.raises(PoiseError):
        project_tools(settings).apply(request)
    assert source.read_bytes() == before
    assert not (project['root'] / request['destination']).exists()


def test_complete_pending_setup_uses_current_graph_without_old_sources(project):
    settings, blueprint, request, graph = external_request(project)
    from poise.modules.projects.domain import ProjectBlueprint
    manifest = project['root'] / request['destination'] / 'project.json'
    config = ProjectBlueprint.parse(blueprint).build(request['edits'], 1000)
    write_json(manifest, config)
    write_json(graph, project['workflow'])
    graph_before = graph.read_bytes(); manifest_before = manifest.read_bytes()
    marker = manifest.parent / '.project-setup-pending.json'
    write_json(marker, {'schema': 'project-setup-pending-1', 'request_id': request['request_id'],
                       'project': 'pilot', 'workflows': {'development': str(graph)}})
    (project['root'] / 'config/workflows/development.json').unlink()
    (project['root'] / 'config/project-blueprint.json').unlink()
    result = project_tools(settings).apply(request)
    assert result['replayed'] is True
    assert manifest.read_bytes() == manifest_before
    assert graph.read_bytes() == graph_before
    assert not marker.exists()
    registry = json.loads((project['root'] / 'state/project-registry.json').read_text())
    assert registry['projects']['pilot']['config_path'] == request['destination'] + '/project.json'


@pytest.mark.parametrize('case', ['literal_project', 'relative_dot_binding'])
def test_replay_accepts_same_valid_creation_inputs_and_registry_path(project, case):
    settings, blueprint, request = setup_case(project)
    if case == 'literal_project':
        blueprint['config']['project'] = 'pilot'
        blueprint['questions'] = [q for q in blueprint['questions'] if q['path'] != ['project']]
        request['edits'] = [edit for edit in request['edits'] if edit['path'] != ['project']]
        write_json(project['root'] / 'config/project-blueprint.json', blueprint)
    commands = project_tools(settings)
    first = commands.apply(request)
    registry = project['root'] / 'state/project-registry.json'
    if case == 'relative_dot_binding':
        raw = json.loads(registry.read_text())
        raw['projects']['pilot']['config_path'] = './' + raw['projects']['pilot']['config_path']
        write_json(registry, raw)
    manifest = Path(first['config_path']); before = manifest.read_bytes()
    assert commands.apply(request)['replayed'] is True
    assert manifest.read_bytes() == before
