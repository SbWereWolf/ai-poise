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
        'process_sources':{'development':{'path':'config/processes/development.json','digest':digest(project['process'])}},
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
        'template':{'id':'selected','version':'1','digest':digest(blueprint)},
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
    root, config, processes = load_config(config_path)
    assert 'runtime_resources' not in config
    registry = project_tools(settings_path).list()
    assert any(item['project'] == config['project'] and item['config_path'] == str(config_path)
               for item in registry['projects'])
    before_processes = {goal: (root / relative).read_bytes()
                        for goal, relative in config['processes'].items()}
    result = project_config_tools(settings_path).apply({
        'schema': 'project-runtime-resources-bootstrap-1',
        'request_id': 'fixture-runtime-resources', 'config_path': str(config_path),
        'expected_revision': digest({'config': config, 'processes': processes}),
        'runtime_resources': {'project_setup': str(settings_path), 'hooks': []},
        'receipt_path': 'operations/fixture-runtime-resources.json',
    })
    _, current, _ = load_config(config_path)
    assert result['status'] == 'completed'
    assert current == {**config, 'runtime_resources': {
        'project_setup': str(settings_path), 'hooks': [],
    }}
    assert all((root / config['processes'][goal]).read_bytes() == content
               for goal, content in before_processes.items())
    return current
