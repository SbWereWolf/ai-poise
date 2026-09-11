import sys
from pathlib import Path
from copy import deepcopy
from conftest import write_json


def command_probe(name='python', code='print("ready")', required=True):
    return {'id':name, 'kind':'command', 'required':required,
            'argv':[sys.executable,'-B','-c',code], 'cwd':'${workspace}', 'environment':{},
            'timeout_seconds':3.0,'max_output_bytes':65536,
            'expected_exit_code':0, 'stdout_contains':['ready'],'stderr_contains':[],
            'json_assertions':[], 'mcp':None,
            'tool_ref':'python', 'project_bound':False,
            'remediation':'Install the explicitly required interpreter and retry.'}


def definition():
    return {'schema':'hook-definition-1','id':'primary-hooks','agent_id':'primary',
            'message_source':'codex-hook-main',
            'events':[
                {'event':'SessionStart','matcher':'startup|resume|compact|clear','timeout_seconds':15,
                 'async':False,'context_limit':2000,'status_message':'Bind Harness session'},
                {'event':'UserPromptSubmit','matcher':'','timeout_seconds':15,'async':False,
                 'context_limit':2000,'status_message':'Record user turn'},
                {'event':'Stop','matcher':'','timeout_seconds':5,'async':False,
                 'context_limit':None,'status_message':'Report Harness state'},
                {'event':'SessionEnd','matcher':'other','timeout_seconds':3,'async':False,
                 'context_limit':None,'status_message':'Record session end'}],
            'probes':[command_probe()], 'gate_operations':['bootstrap','verify','artifacts'],
            'context_template':'Use {launcher} with a single work JSON packet. Current state: {state}.',
            'stop_template':'Current Harness state: {state}; no acceptance or transition was performed.',
            'unknown_outcome_message':'Native hook failed; inspect the local operational record.'}


def settings(project,tmp_path):
    root=project['root']
    project['cfg']['batch']['message_source']={'id':'codex-hook-main','mode':'runtime_event'}
    # Hook transport tests exercise message identity and process routing, not elapsed-time
    # accounting. Reported mode keeps those process-boundary tests independent of host
    # wall-clock adjustments while the accounting suite retains tool-cycle coverage.
    project['cfg']['accounting']['time_mode']='reported'
    write_json(project['config_path'],project['cfg'])
    return write_json(root/'hook-settings.json',{
        'schema':'hook-settings-1','root':'.','project_config':'project.json',
        'python':sys.executable,'source_root':str(Path(__file__).resolve().parents[2]/'src'),
        'hooks_file':'.codex/hooks.json','definitions':'config/hook-definitions',
        'state':'state/hook-control','lock':'state/hook-control.lock',
        'database':'hook-state.sqlite','bindings':'bindings','launcher':'work.sh',
        'binding_file':'binding.json','observations':'observations','response_file':'response.json',
        'cwd_roots':[str(tmp_path)],'file_mode':384,'executable_mode':448,
        'lock_seconds':3.0,'lock_poll_seconds':0.01,
        'max_input_bytes':1048576,'output_chars':5000,'max_probes':30,
        'probe_files':{'stdout':'stdout.txt','stderr':'stderr.txt','receipt':'receipt.json'},
        'exit_codes':{'success':0,'incomplete':1,'rejected':2}})


def install(service, d=None, request_id='install-1', revision=None):
    return service.install({'request_id':request_id,'expected_revision':revision,'definition':definition() if d is None else d})


def event(name='SessionStart',session='conversation',turn='turn1'):
    e={'session_id':session,'transcript_path':None,'cwd':None,'hook_event_name':name}
    if name=='SessionStart': e['source']='startup'
    if name in ('UserPromptSubmit','Stop'):e['turn_id']=turn
    if name=='UserPromptSubmit':e['prompt']='private user content'
    if name=='Stop':e['stop_hook_active']=False;e['last_assistant_message']='private assistant content'
    if name=='SessionEnd':e['reason']='other'
    return e
