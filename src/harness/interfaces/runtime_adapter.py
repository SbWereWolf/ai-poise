"""Explicit source adapter for the existing declarative Work API."""
from ..common import HarnessError,read_json,load_config
from ..infrastructure.runtime_adapter import RuntimeAdapter
from ..infrastructure.goal_config import strict_json
from .work import write_result
from pathlib import Path


def execute(settings_path,stream,output,error):
    adapter=None
    try:
        settings=read_json(settings_path)
        adapter=RuntimeAdapter(settings)
        _,cfg,_=load_config(Path(settings['project_config']))
        raw=stream.read(cfg['batch']['max_input_bytes']+1)
        if len(raw)>cfg['batch']['max_input_bytes']:raise HarnessError('Runtime packet exceeds input limit')
        packet=strict_json(raw.decode('utf-8'))
        result=adapter.invoke(packet)
        write_result(adapter.runtime,result,output)
        return 1 if result['status'] in ('checks_failed','content_requirements_failed','evidence_requirements_failed','observations_stale','action_failed','action_blocked') else 0
    except (HarnessError,UnicodeError,RecursionError) as exc:
        if adapter is not None and hasattr(adapter,'runtime'):
            write_result(adapter.runtime,{'status':'rejected','reason':str(exc)},output)
        else:error.write(str(exc)+'\n')
        return 2
