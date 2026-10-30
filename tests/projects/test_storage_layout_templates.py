"""New project publications use the agreed project root and independent stores."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from poise.common import load_config
from poise.composition import project_tools
from poise.infrastructure.hook_transport import HookSettings
from poise.infrastructure.projects import ProjectSettings, FileProjectSetup

from .helpers import shipped_setup, shipped_edits, fill_markers

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['common'])
def test_new_project_publishes_separate_stores_at_explicit_project_root(tmp_path, name):
    tools = tmp_path / 'tools'
    settings_path = shipped_setup(tools)
    settings = ProjectSettings(settings_path)
    blueprint = FileProjectSetup(settings).template({'id': name})
    assert blueprint.data['config']['paths']['database'] == 'tasks/tasks.sqlite'
    assert blueprint.data['config']['paths']['requirements_database'] == '${PROJECT_DATA_ROOT}/requirements/requirements.sqlite'
    data = tmp_path / 'project-data' / 'demo'
    trees = tmp_path / 'code' / 'worktrees'
    edits = shipped_edits(blueprint.data, tools=tools, project='demo', repository=tmp_path/'code',
                          data=data, trees=trees)
    result = project_tools(settings_path).apply({
        'schema': 'project-setup-1', 'request_id': 'new-project-layout',
        'destination': 'projects/demo/config', 'template': {
            'id': name,
        }, 'edits': edits, 'probe_repository': False,
    })
    assert result['status'] == 'created'
    _, config, workflows = load_config(Path(result['config_path']))
    assert config['paths']['state'] == str(data)
    assert config['paths']['database'] == 'tasks/tasks.sqlite'
    assert config['paths']['lock'] == 'tasks/tasks.lock'
    assert config['paths']['requirements_database'] == str(data / 'requirements/requirements.sqlite')
    assert config['paths']['requirements_lock'] == str(data / 'requirements/requirements.lock')
    assert config['accounting']['storage'] == {
        'database': 'telemetry/events.sqlite', 'lock': 'telemetry/events.lock',
    }
    assert config['paths']['worktrees'] == str(trees)
    assert len(workflows) == 13
    assert (data / 'workflows/development.json').is_file()
    assert not (data / 'tasks/tasks.sqlite').exists()
    assert not (data / 'requirements/requirements.sqlite').exists()
    assert not trees.exists()
    assert not (tools / 'projects/demo/config/state').exists()


def test_hook_example_validates_independent_registry_with_actual_owner(tmp_path):
    example = json.loads((ROOT / 'config/hook-settings.example.json').read_text())
    example = fill_markers(example, {'FILESYSTEM_ROOT': tmp_path, 'PROJECT_DATA_PARENT_ROOT': tmp_path/'poise-project-data',
                                     'PROJECT_DATA_ROOT': tmp_path/'poise-project-data/demo',
                                     'PROJECT_CONFIG_ROOT': tmp_path/'config', 'POISE_SOURCE_ROOT': ROOT,
                                     'PYTHON_EXECUTABLE': sys.executable, 'PYTHON_PACKAGE_ROOT': ROOT/'src'})
    example.update(root=str(tmp_path), python=sys.executable, source_root=str(ROOT / 'src'),
                   cwd_roots=[str(tmp_path)])
    settings = HookSettings.proposed(tmp_path / 'hook-settings.json', example)
    assert settings.database == tmp_path / 'poise-project-data/poise-op/codex-hook/sessions-runtime.sqlite'
    assert settings.bindings == settings.state / 'bindings'
    assert settings.observations == settings.state / 'probes'
    assert not settings.state.exists()
    assert not settings.database.exists()


def test_manifest_example_uses_canonical_stores_and_validates_with_workflow_owner(tmp_path):
    example = json.loads((ROOT / 'config/project.example.json').read_text())
    assert example['paths']['database'] == 'tasks/tasks.sqlite'
    assert example['paths']['lock'] == 'tasks/tasks.lock'
    assert example['paths']['requirements_database'] == '${PROJECT_DATA_ROOT}/requirements/requirements.sqlite'
    assert example['paths']['requirements_lock'] == '${PROJECT_DATA_ROOT}/requirements/requirements.lock'
    assert example['accounting']['storage'] == {
        'database': 'telemetry/events.sqlite', 'lock': 'telemetry/events.lock',
    }
    data = tmp_path / 'project-data/demo'
    example = fill_markers(example, {'PROJECT_DATA_ROOT': data, 'PROJECT_CONFIG_ROOT': tmp_path/'configuration',
                                    'POISE_SOURCE_ROOT': ROOT, 'REPOSITORY_ROOT': tmp_path/'code',
                                    'PROJECT_ID': 'demo', 'COMMIT_AUTHOR_NAME': 'Fixture',
                                    'COMMIT_AUTHOR_EMAIL': 'fixture@example.invalid'})
    example['paths']['worktrees'] = str(tmp_path / 'code/worktrees')
    example['git']['push_required'] = False
    example['runtime_resources'] = {'project_setup': str(ROOT/'config/project-setup.json'), 'hooks': []}
    manifest = tmp_path / 'configuration/project.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(example))
    for goal, relative in example['workflows'].items():
        target = manifest.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'config/catalogue/workflows' / (goal + '.json'), target)
    _, actual, _ = load_config(manifest)
    assert actual['paths']['state'] == str(data)
    assert (data / 'workflows/development.json').is_file()
    assert not (data / 'tasks/tasks.sqlite').exists()


@pytest.mark.parametrize('name', ['common'])
def test_missing_worktrees_answer_refuses_before_project_or_data_publication(tmp_path, name):
    tools = tmp_path / 'tools'
    settings_path = shipped_setup(tools)
    registry_path = tools/'config/project-registry.json'
    before = registry_path.read_bytes()
    settings = ProjectSettings(settings_path)
    selection = {'id':name}
    blueprint = FileProjectSetup(settings).template(selection)
    assert any(q['id']=='worktrees' and q['type']=='text' for q in blueprint.data['questions'])
    edits = shipped_edits(blueprint.data, tools=tools, project='demo', repository=tmp_path/'code',
                          data=tmp_path/'project-data/demo', trees=tmp_path/'code/worktrees')
    from poise.common import PoiseError
    with pytest.raises(PoiseError, match='worktrees'):
        project_tools(settings_path).apply({
            'schema':'project-setup-1','request_id':'missing-worktrees',
            'destination':'projects/demo/config','template':selection,
            'edits':[edit for edit in edits if edit['path'] != ['paths','worktrees']],
            'probe_repository':False,
        })
    assert registry_path.read_bytes()==before
    assert not (tools/'projects/demo').exists()
    assert not (tmp_path/'project-data').exists()
    assert not (tmp_path/'code/worktrees').exists()


def test_pilot_request_accepts_explicit_roots_and_stops_before_task_work(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('storage_pilot', ROOT / 'examples/project_pilot.py')
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    tools = tmp_path / 'tools'
    shipped_setup(tools)
    captured = []

    class BeforePublication(Exception):
        pass

    class RequestBoundary:
        def apply(self, request):
            captured.append(request)
            raise BeforePublication

    monkeypatch.setattr(pilot, 'project_tools', lambda settings: RequestBoundary())
    data = tmp_path / 'project-data/demo'
    trees = tmp_path / 'code/worktrees'
    with pytest.raises(BeforePublication):
        pilot.run(tools, tmp_path / 'code', 'main', 'projects/demo/config', 'PILOT',
                  requirements_root=tmp_path / 'requirements', data_root=data,
                  worktrees_root=trees)
    assert len(captured) == 1
    edits = {tuple(item['path']): item['value'] for item in captured[0]['edits']}
    assert edits[('paths', 'state')] == str(data)
    assert edits[('paths', 'worktrees')] == str(trees)
    assert not data.exists() and not trees.exists()
    assert not (tools / 'projects/demo/config').exists()


def test_all_new_project_workflow_sources_declare_purpose_without_implicit_authority():
    from poise.modules.goal_config.domain import GoalTypeDefinition
    sources = ROOT / 'config/catalogue/workflows'
    for goal in ('analysis','design','development','documentation','environment_diagnostics',
                 'environment_remediation','integration','profiling','review','sprint_planning',
                 'task_planning','test_development','verification'):
        raw = json.loads((sources / (goal + '.json')).read_text())
        GoalTypeDefinition.from_configuration(raw)
        for stage in raw['stages']:
            assert stage['purpose'] in {'design','execution','observation','other'}
            assert stage['criteria_authority'] == {'propose': [], 'decide': []}
        stages = {s['id']: s for s in raw['stages']}
        if goal == 'design':assert stages['detailed_design']['purpose'] == 'execution'
        if goal == 'analysis':assert stages['challenge']['purpose'] == 'observation'
        if 'publication' in stages:assert stages['publication']['purpose'] == 'other'
