import json
from copy import deepcopy
from pathlib import Path
from poise.common import digest
from tests.conftest import write_json


def setup_case(project):
    root=project['root']
    config = deepcopy(project['cfg'])
    if 'runtime_resources' in config:
        config['runtime_resources']['project_setup'] = str(root / 'setup.json')
    blueprint={
        'schema':'project-blueprint-1', 'version':'1',
        'config':config,
        'workflow_sources':{'development':{'path':'config/workflows/development.json','digest':digest(project['workflow'])}},
        'questions':[
            {'id':'project','path':['project'],'type':'text','prompt':'Project ID'},
            {'id':'repository','path':['git','repository'],'type':'text','prompt':'Repository'},
            {'id':'push','path':['git','push_required'],'type':'boolean','prompt':'Require push'}]
    }
    write_json(root/'config/project-blueprint.json',blueprint)
    settings={'schema':'project-setup-settings-1','root':'.','templates':{
        'selected':{'path':'config/project-blueprint.json','version':'1','digest':digest(blueprint)}},
        'manifest':'project.json','receipt':'setup-receipt.json',
        'registry':'state/project-registry.json','lock':'state/project-setup.lock',
        'file_mode':384,'directory_mode':448,'json_indent':2,
        'lock_seconds':2.0,'lock_poll_seconds':0.01,'git_seconds':15.0,
        'max_input_bytes':1048576,'max_edits':1000,'max_survey_steps':100,'output_chars':8192,
        'exit_codes':{'success':0,'rejected':2,'aborted':3}}
    settings_path=write_json(root/'setup.json',settings)
    write_json(root/'state/project-registry.json',{
        'schema':'configured-project-registry-1','projects':{}})
    request={'schema':'project-setup-1','request_id':'setup-1','destination':'configured/pilot',
        'template':{'id':'selected'},
        'edits':[{'path':['project'],'value':'pilot'},
                 {'path':['git','repository'],'value':str(project['app'])},
                 {'path':['git','push_required'],'value':False}],
        'probe_repository':True}
    return settings_path,blueprint,request


def bootstrap_fixture_resources(settings_path, config_path):
    """Explicit fixture preparation through the real initial-publication owner."""
    from poise.common import load_config
    from poise.composition import project_config_tools, project_tools
    settings_path, config_path = Path(settings_path), Path(config_path)
    root, config, workflows = load_config(config_path)
    assert 'runtime_resources' not in config
    registry = project_tools(settings_path).list()
    assert any(item['project'] == config['project'] and item['config_path'] == str(config_path)
               for item in registry['projects'])
    before_workflows = {goal: (root / relative).read_bytes()
                        for goal, relative in config['workflows'].items()}
    result = project_config_tools(settings_path).apply({
        'schema': 'project-runtime-resources-bootstrap-1',
        'request_id': 'fixture-runtime-resources', 'config_path': str(config_path),
        'expected_revision': digest({'config': config, 'workflows': workflows}),
        'runtime_resources': {'project_setup': str(settings_path), 'hooks': []},
        'receipt_path': 'operations/fixture-runtime-resources.json',
    })
    _, current, _ = load_config(config_path)
    assert result['status'] == 'completed'
    assert current == {**config, 'runtime_resources': {
        'project_setup': str(settings_path), 'hooks': [],
    }}
    assert all((root / config['workflows'][goal]).read_bytes() == content
               for goal, content in before_workflows.items())
    return current


def fill_markers(value, replacements):
    """Explicit fixture input filling, not a production template renderer."""
    if isinstance(value, dict):
        return {key: fill_markers(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [fill_markers(item, replacements) for item in value]
    if isinstance(value, str):
        for key, replacement in replacements.items():
            value = value.replace('${' + key + '}', str(replacement))
    return value


def shipped_setup(tools):
    """Mount the shipped config tree at an explicit test-owned setup root."""
    import shutil
    source = Path(__file__).resolve().parents[2]
    shutil.copytree(source / 'config', tools / 'config')
    settings = json.loads((tools / 'config/project-setup.example.json').read_text())
    settings.update(root=str(tools), registry='config/project-registry.json', lock='setup.lock')
    for entry in settings['templates'].values():
        entry['path'] = entry['path'].removeprefix('app/')
        path = tools / entry['path']
        blueprint = json.loads(path.read_text())
        for item in blueprint['workflow_sources'].values():
            item['path'] = item['path'].removeprefix('app/')
        write_json(path, blueprint)
    write_json(tools / 'config/project-registry.json', {'schema': 'configured-project-registry-1', 'projects': {}})
    return write_json(tools / 'config/project-setup.json', settings)


def shipped_edits(blueprint, *, tools, project, repository, data, trees, base='main'):
    config = fill_markers(blueprint['config'], {
        'PROJECT_ID': project, 'POISE_SOURCE_ROOT': tools,
        'PROJECT_CONFIG_ROOT': tools / 'projects' / project / 'config',
        'PROJECT_DATA_ROOT': data, 'REPOSITORY_ROOT': repository,
        'COMMIT_AUTHOR_NAME': 'Fixture', 'COMMIT_AUTHOR_EMAIL': 'fixture@example.invalid',
    })
    config['paths']['worktrees'] = str(trees)
    config['git'].update(base_ref=base, push_required=False)
    config['runtime_resources'] = {'project_setup': str(tools / 'config/project-setup.json'), 'hooks': []}
    edits = []
    for question in blueprint['questions']:
        value = config
        for key in question['path']:
            value = value[key]
        edits.append({'path': question['path'], 'value': value})
    edits.append({'path': ['runtime_resources', 'hooks'], 'value': []})
    return edits
