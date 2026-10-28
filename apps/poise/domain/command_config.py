"""Explicit known command settings; no filesystem or Session effects."""
from copy import deepcopy
from dataclasses import dataclass
import math

from domain.generic import Problem, positive_count
from domain.session_config import configured_text
from domain.state import invalid_config, required


@dataclass(frozen=True)
class CommandSettings:
    store: str
    output: str
    mode: int
    files: dict
    limits: dict


def parse_settings(raw):
    if 'commands' not in raw:
        raise Problem('commands_not_configured', {}, 'configure:commands', 'unavailable')
    value=required(raw['commands'], {'schema','request_store','output_root','file_mode','files','limits'}, '/commands')
    if value['schema']!='poise-command-config/v1':
        invalid_config('/commands/schema')
    store=configured_text(value['request_store'],'/commands/request_store')
    output=configured_text(value['output_root'],'/commands/output_root')
    mode=value['file_mode']
    if type(mode) is not int or not 0<=mode<=0o777:
        invalid_config('/commands/file_mode')
    names=required(value['files'], {'stdout','stderr','result'}, '/commands/files')
    files={}
    for key in ('stdout','stderr','result'):
        name=configured_text(names[key],'/commands/files/'+key)
        if name in ('.','..') or '/' in name:
            invalid_config('/commands/files/'+key)
        files[key]=name
    if len(set(files.values()))!=3:
        invalid_config('/commands/files')
    integers=('id_bytes','poll_ms','capture_chunk_bytes','terminate_grace_ms',
              'completion_budget_ms','delivery_margin_ms','transport_budget_ms',
              'transport_margin_ms','mcp_max_message_bytes','mcp_max_messages')
    limits=required(value['limits'],set(integers)|{'sqlite_timeout_seconds'},'/commands/limits')
    for key in integers:
        if not positive_count(limits[key]):
            invalid_config('/commands/limits/'+key)
    timeout=limits['sqlite_timeout_seconds']
    try:
        valid=type(timeout) in (int,float) and timeout>=0 and math.isfinite(timeout)
    except OverflowError:
        valid=False
    if not valid:
        invalid_config('/commands/limits/sqlite_timeout_seconds')
    if limits['transport_budget_ms']<limits['completion_budget_ms']+limits['transport_margin_ms']:
        invalid_config('/commands/limits/transport_budget_ms')
    return CommandSettings(store,output,mode,files,{k:limits[k] for k in (*integers,'sqlite_timeout_seconds')})


def execution_settings(settings):
    keys=('poll_ms','capture_chunk_bytes','terminate_grace_ms','mcp_max_message_bytes','mcp_max_messages')
    return {'output_root':settings.output,'file_mode':settings.mode,'files':deepcopy(settings.files),
            'limits':{key:settings.limits[key] for key in keys}}
