"""CLI envelopes for managed configuration, native hooks and bound work."""
import json
from pathlib import Path
import uuid
from ..common import PoiseError,exact_keys,load_config
from ..infrastructure.hook_transport import HookService
from ..infrastructure.goal_config import strict_json,atomic_write
from ..modules.work.domain import BUSINESS_INCOMPLETE_STATUSES
from .work import write_result


def _write(service,result,output):
    settings=service.settings
    text=json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n'
    if len(text)>settings.raw['output_chars']:
        path=settings.observations/uuid.uuid4().hex/settings.raw['response_file']
        atomic_write(path,(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),settings.raw['file_mode'])
        text=json.dumps({'status':result['status'],'response_path':str(path)},ensure_ascii=False)+'\n'
    output.write(text)


def execute(command,args,stream,output,error):
    service=None
    try:
        service=HookService(args.settings);cfg=service.settings.raw
        raw=stream.read(cfg['max_input_bytes']+1)
        if len(raw)>cfg['max_input_bytes']:raise PoiseError('Hook/adapter packet exceeds configured input limit')
        packet=strict_json(raw.decode('utf-8'))
        if command=='hook':
            result=service.event(args.definition,packet)
            if len(json.dumps(result,ensure_ascii=False))>cfg['output_chars']:
                raise PoiseError('Configured hook context cannot fit output limit')
            output.write(json.dumps(result,ensure_ascii=False)+'\n')
            return cfg['exit_codes']['success']
        if command=='hook-work':
            result=service.work(args.binding,packet)
            write_result(service.runtime,result,output)
            bad = result['status'] in BUSINESS_INCOMPLETE_STATUSES
            return cfg['exit_codes']['incomplete' if bad else 'success']
        exact_keys(packet,{'operation','input'},'runtime-config packet')
        if packet['operation'] in ('install','reconcile'):
            action=service.install if packet['operation']=='install' else service.reconcile
            result=action(packet['input'])
            _,project,_=load_config(service.settings.project_config)
            checks=service.probes(result['definition_path'],project['git']['repository'])
            result={**result,'capability_checks':checks}
        elif packet['operation']=='diagnose':
            result=service.diagnose(packet['input'])
            _write(service,result,output)
            return cfg['exit_codes']['success' if result['status']=='diagnosed' else 'incomplete']
        elif packet['operation']=='probe':
            exact_keys(packet['input'],{'definition_path','workspace'},'probe packet')
            value=packet['input']
            result={'status':'probed','capability_checks':service.probes(value['definition_path'],value['workspace'])}
        else:raise PoiseError('Unknown runtime-config operation')
        _write(service,result,output)
        return cfg['exit_codes']['success' if result['capability_checks']['ready'] else 'incomplete']
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
        return service.settings.raw['exit_codes']['success' if result['capability_checks']['ready'] else 'incomplete']
    except (PoiseError,OSError,UnicodeError,RecursionError) as exc:
        error.write(str(exc)+'\n');return 2
