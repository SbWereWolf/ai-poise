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

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['linux-reference', 'wsl-poise'])
def test_new_project_publishes_separate_stores_at_explicit_project_root(tmp_path, name):
    tools = tmp_path / 'tools'
    shutil.copytree(ROOT / 'config', tools / 'config')
    settings_path = tools / 'config/project-setup.example.json'
    (tools / 'config/project-registry.json').write_text(json.dumps({
        'schema': 'configured-project-registry-1', 'projects': {},
    }))
    settings = ProjectSettings(settings_path)
    entry = settings.raw['templates'][name]
    blueprint = FileProjectSetup(settings).template({'id': name, **{
        key: entry[key] for key in ('version', 'digest')
    }})
    assert blueprint.data['config']['paths']['database'] == 'tasks/tasks.sqlite'
    assert blueprint.data['config']['paths']['requirements_database'] == 'requirements/requirements.sqlite'
    data = tmp_path / 'project-data' / 'demo'
    trees = tmp_path / 'code' / 'worktrees'
    values = {'project': 'demo', 'repository': str(tmp_path / 'code'), 'base': 'main',
              'remote': 'origin', 'author_name': 'Fixture',
              'author_email': 'fixture@example.invalid', 'push': False,
              'state': str(data), 'worktrees': str(trees), 'environment': ['PATH']}
    edits = [{'path': question['path'], 'value': values[question['id']]}
             for question in blueprint.data['questions']]
    result = project_tools(settings_path).apply({
        'schema': 'project-setup-1', 'request_id': 'new-project-layout',
        'destination': 'projects/demo/config', 'template': {
            'id': name, 'version': entry['version'], 'digest': entry['digest'],
        }, 'edits': edits, 'probe_repository': False,
    })
    assert result['status'] == 'created'
    _, config, workflows = load_config(Path(result['config_path']))
    assert config['paths']['state'] == str(data)
    assert config['paths']['database'] == 'tasks/tasks.sqlite'
    assert config['paths']['lock'] == 'tasks/tasks.lock'
    assert config['paths']['requirements_database'] == 'requirements/requirements.sqlite'
    assert config['paths']['requirements_lock'] == 'requirements/requirements.lock'
    assert config['accounting']['storage'] == {
        'database': 'telemetry/events.sqlite', 'lock': 'telemetry/events.lock',
    }
    assert config['paths']['worktrees'] == str(trees)
    assert len(workflows) == 13
    assert not data.exists()  # Publication is configuration, not initialization/migration.
    assert not trees.exists()
    assert not (tools / 'projects/demo/config/state').exists()


def test_hook_example_validates_independent_registry_with_actual_owner(tmp_path):
    example = json.loads((ROOT / 'config/hook-settings.example.json').read_text())
    assert example['state'] == 'poise-project-data/demo/codex-hook'
    assert example['database'] == 'sessions-runtime.sqlite'
    assert example['lock'] == 'poise-project-data/demo/codex-hook/sessions-runtime.lock'
    example.update(root=str(tmp_path), python=sys.executable, source_root=str(ROOT / 'src'),
                   cwd_roots=[str(tmp_path)])
    settings = HookSettings.proposed(tmp_path / 'hook-settings.json', example)
    assert settings.database == tmp_path / 'poise-project-data/demo/codex-hook/sessions-runtime.sqlite'
    assert settings.bindings == settings.state / 'bindings'
    assert settings.observations == settings.state / 'probes'
    assert not settings.state.exists()
    assert not settings.database.exists()


def test_manifest_example_uses_canonical_stores_and_validates_with_workflow_owner(tmp_path):
    example = json.loads((ROOT / 'config/project.example.json').read_text())
    assert example['paths']['database'] == 'tasks/tasks.sqlite'
    assert example['paths']['lock'] == 'tasks/tasks.lock'
    assert example['paths']['requirements_database'] == 'requirements/requirements.sqlite'
    assert example['paths']['requirements_lock'] == 'requirements/requirements.lock'
    assert example['accounting']['storage'] == {
        'database': 'telemetry/events.sqlite', 'lock': 'telemetry/events.lock',
    }
    data = tmp_path / 'project-data/demo'
    example['paths']['state'] = str(data)
    example['paths']['worktrees'] = str(tmp_path / 'code/worktrees')
    example['git']['repository'] = str(tmp_path / 'code')
    example['git']['push_required'] = False
    manifest = tmp_path / 'configuration/project.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(example))
    for goal, relative in example['workflows'].items():
        target = manifest.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'config/catalogue/workflows' / (goal + '.json'), target)
    _, actual, _ = load_config(manifest)
    assert actual['paths']['state'] == str(data)
    assert not data.exists()


@pytest.mark.parametrize('name', ['linux-reference', 'wsl-poise'])
def test_missing_worktrees_answer_refuses_before_project_or_data_publication(tmp_path, name):
    tools = tmp_path / 'tools'
    shutil.copytree(ROOT / 'config', tools / 'config')
    settings_path = tools / 'config/project-setup.example.json'
    registry_path = tools / 'config/project-registry.json'
    registry_path.write_text(json.dumps({'schema':'configured-project-registry-1','projects':{}}))
    before = registry_path.read_bytes()
    settings = ProjectSettings(settings_path)
    entry = settings.raw['templates'][name]
    selection = {'id':name, **{k:entry[k] for k in ('version','digest')}}
    blueprint = FileProjectSetup(settings).template(selection)
    assert any(q['id']=='worktrees' and q['type']=='text' for q in blueprint.data['questions'])
    values = {'project':'demo','repository':str(tmp_path/'code'),'base':'main','remote':'origin',
              'author_name':'Fixture','author_email':'fixture@example.invalid','push':False,
              'state':str(tmp_path/'project-data/demo'),'environment':['PATH']}
    from poise.common import PoiseError
    with pytest.raises(PoiseError, match='worktrees'):
        project_tools(settings_path).apply({
            'schema':'project-setup-1','request_id':'missing-worktrees',
            'destination':'projects/demo/config','template':selection,
            'edits':[{'path':q['path'],'value':values[q['id']]}
                     for q in blueprint.data['questions'] if q['id']!='worktrees'],
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
    shutil.copytree(ROOT / 'config', tools / 'config')
    shutil.copyfile(tools / 'config/project-setup.example.json', tools / 'config/project-setup.json')
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
