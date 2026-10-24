"""CLI envelopes for managed configuration, native hooks and bound work."""
from pathlib import Path
import uuid
import time
from ..common import PoiseError,exact_keys
from ..infrastructure.harness_config import load_harness_config
from ..infrastructure.hook_transport import HookService, HookSettings, matches_repository_scope
from ..infrastructure.goal_config import strict_json,atomic_write
from ..modules.work.domain import BUSINESS_INCOMPLETE_STATUSES
from ..modules.result_views.domain import (
    bounded_envelope, native_context_view, require_result_budget, utf8_json_text,
)


def _save_response(service,result):
    settings=service.settings
    path=settings.observations/uuid.uuid4().hex/settings.raw['response_file']
    atomic_write(path,(utf8_json_text(result,indent=2)+'\n').encode('utf-8'),settings.raw['file_mode'])
    return path


def _write(service,result,output):
    path=_save_response(service,result)
    settings=service.settings
    output.write(bounded_envelope(result,path,settings.raw['output_chars'],{}, {}, []))


def _write_native(service,result,definition_path,output):
    cfg=service.settings.raw
    if 'systemMessage' in result and 'hookSpecificOutput' in result:
        path=_save_response(service,result)
        definition = service.definition(definition_path).data
        notice=(definition['transport']['unknown_outcome_message']
                if definition['schema']=='hook-definition-2' else definition['unknown_outcome_message'])
        text=native_context_view(result,path,cfg['output_chars'],notice)
    else:
        text=utf8_json_text(result)+'\n'
        if len(text)>cfg['output_chars']:
            raise PoiseError('Configured hook context cannot fit output limit')
    output.write(text)


def execute(command,args,stream,output,error, *, started_at=None):
    started_at=time.monotonic() if started_at is None else started_at
    service=None
    try:
        settings=HookSettings(args.settings);cfg=settings.raw
        if command=='hook' and getattr(args,'retry',None) is not None:
            service=HookService(args.settings)
            result=service.retry_lifecycle(args.definition,args.retry)
            _write_native(service,result,args.definition,output)
            return cfg['exit_codes']['success']
        raw=stream.read(cfg['max_input_bytes']+1)
        if len(raw)>cfg['max_input_bytes']:raise PoiseError('Hook/adapter packet exceeds configured input limit')
        packet=strict_json(raw.decode('utf-8'))
        if command == 'hook' and not isinstance(packet, dict):
            raise PoiseError('Native hook JSON object required')
        if (command == 'hook' and packet.get('hook_event_name') not in ('PreToolUse','PostToolUse')
                and not matches_repository_scope(settings, packet.get('cwd'))):
            output.write('{}\n')
            return cfg['exit_codes']['success']
        service=HookService(args.settings)
        if command=='hook':
            result=service.event(args.definition,packet,started_at=started_at)
            _write_native(service,result,args.definition,output)
            return cfg['exit_codes']['success']
        if command=='hook-work':
            from .work import write_result
            require_result_budget('',cfg['output_chars'],'rejected')
            result=service.work(args.binding,packet,getattr(args,'project',None))
            write_result(service.runtime,result,output)
            bad = result['status'] in BUSINESS_INCOMPLETE_STATUSES
            return cfg['exit_codes']['incomplete' if bad else 'success']
        exact_keys(packet,{'operation','input'},'runtime-config packet')
        predicted=service.settings.observations/uuid.uuid4().hex/cfg['response_file']
        require_result_budget(predicted,cfg['output_chars'],'inconclusive')
        if packet['operation'] in ('install','reconcile','reconfigure'):
            action=getattr(service,packet['operation'])
            result=action(packet['input'])
            if packet['operation'] == 'reconfigure':
                service=HookService(args.settings)
            if (packet['operation'] == 'reconfigure'
                    and 'repository_scope' in packet['input']['settings']):
                current=HookSettings(args.settings)
                workspace=current.raw['repository_scope']['repository']
            else:
                _,project,_=load_harness_config(service.settings.project_config)
                workspace=project['git']['repository']
            checks=service.probes(result['definition_path'],workspace,required=False)
            result={**result,'capability_checks':checks}
        elif packet['operation']=='diagnose':
            result=service.diagnose(packet['input'])
            _write(service,result,output)
            return cfg['exit_codes']['success' if result['status']=='diagnosed' else 'incomplete']
        elif packet['operation']=='probe':
            exact_keys(packet['input'],{'definition_path','workspace'},'probe packet')
            value=packet['input']
            result={'status':'probed','capability_checks':service.probes(value['definition_path'],value['workspace'])}
        elif packet['operation'] in ('summary', 'refresh'):
            action = service.read_summary if packet['operation'] == 'summary' else service.refresh_summary
            summary = action(packet['input'])
            result = {'status': 'summary' if packet['operation'] == 'summary' else 'refreshed',
                      'summary': summary}
            _write(service, result, output)
            return cfg['exit_codes']['success' if summary['ready'] else 'incomplete']
        else:raise PoiseError('Unknown runtime-config operation')
        _write(service,result,output)
        checks = result['capability_checks']
        return cfg['exit_codes']['success' if checks is None or checks['ready'] else 'incomplete']
    except (PoiseError,OSError,UnicodeError,RecursionError) as exc:
        if command=='hook':
            # Codex exit 2 can request a blocking continuation. This transport never
            # uses that protocol on failure: surface the error without creating turns.
            error.write(str(exc)+'\n');return 1
        if service is not None:
            _write(service,{'status':'rejected','reason':str(exc)},output)
            return service.settings.raw['exit_codes']['rejected']
        error.write(str(exc)+'\n');return 2


def setup(args,stream,output,error):
    """Bootstrap has no implicit input limit before a settings file exists."""
    from ..infrastructure.hook_transport import setup_runtime
    from ..modules.capabilities.domain import positive
    try:
        positive(args.max_input_bytes,'max_input_bytes',True)
        raw=stream.read(args.max_input_bytes+1)
        if len(raw)>args.max_input_bytes:raise PoiseError('Setup input exceeds the explicit bootstrap limit')
        packet=strict_json(raw.decode('utf-8'))
        result=setup_runtime(args.settings,packet)
        service=HookService(args.settings)
        _write(service,result,output)
        checks = result['capability_checks']
        return service.settings.raw['exit_codes']['success' if checks is None or checks['ready'] else 'incomplete']
    except (PoiseError,OSError,UnicodeError,RecursionError) as exc:
        error.write(str(exc)+'\n');return 2
