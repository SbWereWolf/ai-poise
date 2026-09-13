import json
from copy import deepcopy
from pathlib import Path
from poise.common import digest
from tests.conftest import write_json


def setup_case(project):
    root=project['root']
    blueprint={
        'schema':'project-blueprint-1', 'version':'1',
        'config':deepcopy(project['cfg']),
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
                 {'path':['git','push_required'],'value':True}],
        'probe_repository':True}
    return settings_path,blueprint,request
