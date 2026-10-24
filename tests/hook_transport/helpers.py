import os
import sys
import json
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
    return {'schema':'hook-definition-2','id':'primary-hooks','agent_id':'primary',
            'message_source':'codex-hook-main',
            'events':[
                {'event':'SessionStart','matcher':'startup|resume|compact|clear','timeout_seconds':15,
                 'async':False,'context_limit':2000,'status_message':'Bind Poise session'},
                {'event':'UserPromptSubmit','matcher':'','timeout_seconds':15,'async':False,
                 'context_limit':2000,'status_message':'Record user turn'},
                {'event':'Stop','matcher':'','timeout_seconds':5,'async':False,
                 'context_limit':None,'status_message':'Report Poise state'},
                {'event':'SessionEnd','matcher':'other','timeout_seconds':3,'async':False,
                 'context_limit':None,'status_message':'Record session end'}],
            'routes': {
                'SessionStart': ['@HOOK_CONFIG@/primary-start.json'],
                'UserPromptSubmit': ['@HOOK_CONFIG@/primary-context.json'],
                'Stop': ['@HOOK_CONFIG@/primary-context.json'],
                'SessionEnd': ['@HOOK_CONFIG@/primary-end.json'],
            },
            'transport': {'unknown_outcome_message':
                          'Native hook failed; inspect the local operational record.'}}


def settings(project,tmp_path):
    if '_prepared_harness' in project:
        return project['_prepared_harness'].settings_path
    root=project['root']
    candidate = deepcopy(project['cfg'])
    candidate['batch']['message_source']={'id':'codex-hook-main','mode':'runtime_event'}
    # Hook transport tests exercise message identity and process routing, not elapsed-time
    # accounting. Reported mode keeps those process-boundary tests independent of host
    # wall-clock adjustments while the accounting suite retains tool-cycle coverage.
    candidate['accounting']['time_mode']='reported'
    managed = False
    if 'runtime_resources' in candidate:
        from poise.composition import project_tools
        selected = candidate['runtime_resources']['project_setup']
        listed = project_tools(selected).list()
        managed = any(item['config_path'] == str(project['config_path'])
                      for item in listed['projects'])
    if not managed:
        # Some tests explicitly request an unmanaged/absent-association input.
        # This builder preserves that input; it never bootstraps it implicitly.
        write_json(project['config_path'], candidate)
    path = write_json(root/'hook-settings.json',{
        'schema':'hook-settings-1','root':'.','project_config':'project.json',
        'python':sys.executable,'source_root':str(Path(__file__).resolve().parents[2]/'src'),
        'hooks_file':'.codex/hooks.json','definitions':'config/hook-definitions',
        'state':'state/hook-control','lock':'state/hook-control.lock',
        'database':'hook-state.sqlite','bindings':'bindings','launcher':'work.sh',
        'binding_file':'binding.json','observations':'observations','response_file':'response.json',
        'cwd_roots':[str(tmp_path)],'file_mode':384,'executable_mode':448,
        'lock_seconds':3.0,'lock_poll_seconds':0.01,
        'max_input_bytes':1048576,'output_chars':5000,'max_probes':30,
        'capability_summary':{
            'file':'capabilities.json',
            'header_template':'Infrastructure ready={ready}; checked_at={checked_at}; workspace={workspace}; snapshot={snapshot_path}',
            'item_template':'{id}: {status}; required={required}; reason={reason}; remediation={remediation}'},
        'probe_files':{'stdout':'stdout.txt','stderr':'stderr.txt','receipt':'receipt.json'},
        'exit_codes':{'success':0,'incomplete':1,'rejected':2}})
    if managed:
        from poise.common import digest, load_config
        from poise.composition import project_config_tools
        from projects.test_update import update_request
        _, current, workflows = load_config(project['config_path'])
        resources = deepcopy(current['runtime_resources'])
        # Hook associations belong to the explicitly published harness profile;
        # the full Task manifest remains the specialised adapter input.
        revision = digest({'config': current, 'workflows': workflows})
        selected_options = {key: candidate[key] for key in ('source_reader', 'telemetry_delivery', 'task_ids')
                            if key in candidate}
        identity = 'fixture-hook-resources-' + digest([revision, resources, selected_options])
        packet = update_request(project['config_path'], revision, request_id=identity,
                                manifest_edits=[
                                    {'path': ['batch', 'message_source'],
                                     'value': candidate['batch']['message_source']},
                                    {'path': ['accounting', 'time_mode'], 'value': 'reported'},
                                    {'path': ['runtime_resources'], 'value': resources},
                                ])
        # Only these explicit fixture selections belong to this bounded correction.
        for option, value in selected_options.items():
            packet['manifest_edits'].append({'path': [option], 'value': value})
        packet['receipt_path'] = 'operations/' + identity + '.json'
        result = project_config_tools(selected).apply(packet)
        assert result['status'] == 'updated'
        _, current, _ = load_config(project['config_path'])
        assert current['runtime_resources'] == resources
        project['cfg'] = current
    else:
        project['cfg'] = candidate
    from tests.hook_transport.architecture_support import GeneralScene
    from poise.common import PoiseError
    if not managed:
        raise PoiseError('Fixture harness publication requires its explicit registered project association')
    raw = json.loads(path.read_text(encoding='utf-8'))
    scene = GeneralScene(project, tmp_path, 'ARCH_FIX_LEGACY_HOOK_FIXTURE', connected=True,
                         settings_path=path, hook_settings=raw, installation_id='primary-hooks')
    scene.publish(request_id='fixture-explicit-task-harness')
    project['_prepared_harness'] = scene
    _handler_files(path)
    return path


def harness_profile(project, parent, *, task_adapter):
    """Select a real explicit profile for a specialised fixture consumer."""
    if project['cfg']['schema'] == 'poise-harness-project-1':
        return project['config_path']
    if task_adapter:
        settings(project, parent)
        return project['_prepared_harness'].path
    from tests.hook_transport.architecture_support import GeneralScene
    scene = GeneralScene(project, parent, 'ARCH_FIX_LEGACY_RESULT_FIXTURE')
    scene.state = project['root'] / project['cfg']['paths']['state']
    scene.profile['paths']['state'] = str(scene.state)
    scene.publish(request_id='fixture-explicit-general-results')
    return scene.path


def _handler_files(settings_path):
    """Materialise versioned test inputs, not a legacy product router."""
    from tests.hook_transport.architecture_support import literal
    path = Path(settings_path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    root = (path.parent / raw['root']).resolve()
    directory = root / 'config/hook-handlers'
    context = literal('task-context-options.json')
    context.update(context_template='Use {launcher} with a single work JSON packet. Current state: {state}.',
                   stop_template='Current Poise state: {state}; no acceptance or transition was performed.')

    def handler(name, options):
        value = deepcopy(literal('handler-configuration.json')['handlers'][0])
        value.update(id=name, implementation={'kind': 'builtin', 'name': name}, options=options)
        return value

    for name, handlers in [
        ('primary-start', [handler('capability-summary', {
            'probes': [command_probe()], 'summary': raw['capability_summary']}), handler('task-context', context)]),
        ('primary-context', [handler('task-context', context)]),
        ('primary-end', [handler('task-runtime-cleanup', {})]),
    ]:
        write_json(directory / (name + '.json'), {
            'schema': 'hook-handler-configuration-1', 'id': name, 'handlers': handlers})
    return directory


def install(service, d=None, request_id='install-1', revision=None):
    chosen = definition() if d is None else deepcopy(d)
    directory = _handler_files(service.settings.path)
    chosen['routes'] = {event: [value.replace('@HOOK_CONFIG@', str(directory)) for value in paths]
                        for event, paths in chosen['routes'].items()}
    return service.install({'request_id':request_id,'expected_revision':revision,'definition':chosen})


def event(name='SessionStart',session='conversation',turn='turn1'):
    e={'session_id':session,'transcript_path':None,'cwd':None,'hook_event_name':name}
    if name=='SessionStart': e['source']='startup'
    if name in ('UserPromptSubmit','Stop'):e['turn_id']=turn
    if name=='UserPromptSubmit':e['prompt']='private user content'
    if name=='Stop':e['stop_hook_active']=False;e['last_assistant_message']='private assistant content'
    if name=='SessionEnd':e['reason']='other'
    return e


def host_environment(session):
    """Explicit simulated host identity; never read identity from a launcher."""
    return {**{key: value for key, value in os.environ.items() if key not in
               ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING')},
            'CODEX_THREAD_ID': session}
