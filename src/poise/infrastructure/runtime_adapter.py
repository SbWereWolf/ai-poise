"""Opt-in local Codex JSONL ingestion and explicit Chat/manual runtime binding.

Only the supported event_msg/user_message source is counted. Tool schemas and
ChatGPT conversation content cannot be introspected by this CLI.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from copy import deepcopy
from ..common import PoiseError,exact_keys,load_config
from ..modules.runtime_adapter.domain import RuntimeIdentity,validate_inventory
from ..modules.session_establishment.domain import CallerIdentity
from ..application.work import WorkTools
from .session_establishment import establish_poise


def validate_settings(cfg):
    exact_keys(cfg,{'schema','project_config','adapter_id','transcript_roots','max_scan_bytes',
                    'max_line_bytes','max_events','required_capabilities'},'runtime adapter settings')
    if cfg['schema']!='runtime-adapter-1':raise PoiseError('Unsupported runtime adapter settings')
    for key in ('project_config','adapter_id'):
        if not isinstance(cfg[key],str) or not cfg[key]:raise PoiseError(f'{key} required')
    for key in ('max_scan_bytes','max_line_bytes','max_events'):
        if type(cfg[key]) is not int or cfg[key]<=0:raise PoiseError(f'{key} must be a positive integer')
    if cfg['max_line_bytes']>cfg['max_scan_bytes']:raise PoiseError('Line bound cannot exceed scan budget')
    roots=cfg['transcript_roots']
    if not isinstance(roots,list) or any(not isinstance(r,str) or not Path(r).is_absolute() for r in roots):
        raise PoiseError('Transcript roots must be explicit absolute directories')
    validate_inventory([],[]) # No defaults or automatic capability probing here.
    if not isinstance(cfg['required_capabilities'],list):raise PoiseError('Explicit required capabilities list required')
    return deepcopy(cfg)


class CodexTranscript:
    def __init__(self,settings):self.config=validate_settings(settings)

    def read(self,path,initial_offset,cursor,conversation):
        p=Path(path)
        roots=[Path(r).resolve() for r in self.config['transcript_roots']]
        if not p.is_absolute() or p.is_symlink() or not any(p.resolve().is_relative_to(r) for r in roots):
            raise PoiseError('Transcript outside explicit roots or symlink')
        if not p.is_file():raise PoiseError('Transcript does not exist')
        if type(initial_offset) is not int or initial_offset<0:raise PoiseError('initial_offset must be explicit nonnegative integer')
        stamp=p.stat();size=stamp.st_size
        offset=initial_offset if cursor is None else cursor['offset']
        if size<offset:raise PoiseError('Transcript truncated; cursor not reset')
        if cursor is not None and [stamp.st_dev,stamp.st_ino]!=cursor['file_id']:
            raise PoiseError('Transcript file replaced; automatic source migration forbidden')
        messages=[];scanned=0;last_start=offset;last_digest=None
        with p.open('rb') as f:
            if cursor is not None and cursor['anchor_digest'] is not None:
                f.seek(cursor['anchor_start'])
                prior=f.read(offset-cursor['anchor_start'])
                if hashlib.sha256(prior).hexdigest()!=cursor['anchor_digest']:
                    raise PoiseError('Transcript previously consumed anchor changed')
                last_start=cursor['anchor_start'];last_digest=cursor['anchor_digest']
            if offset and cursor is None:
                f.seek(offset-1)
                if f.read(1)!=b'\n':raise PoiseError('initial_offset must be a record boundary')
            f.seek(offset)
            while f.tell()<size and scanned<self.config['max_scan_bytes'] and len(messages)<self.config['max_events']:
                start=f.tell()
                line=f.readline(min(self.config['max_line_bytes']+1,size-start))
                if len(line)>self.config['max_line_bytes']:raise PoiseError('Transcript record exceeds explicit line limit')
                if not line.endswith(b'\n'):break # incomplete final event, retry next invocation
                if scanned+len(line)>self.config['max_scan_bytes']:break
                try:record=json.loads(line)
                except (ValueError,UnicodeError) as exc:raise PoiseError(f'Invalid complete transcript record at byte {start}') from exc
                if not isinstance(record,dict):raise PoiseError('Transcript record must be an object')
                payload=record.get('payload')
                if record.get('type')=='event_msg' and isinstance(payload,dict) and payload.get('type')=='user_message':
                    if not isinstance(payload.get('message'),str):raise PoiseError('Unsupported user_message payload')
                    timestamp=record.get('timestamp')
                    if timestamp is not None and not isinstance(timestamp,str):raise PoiseError('Invalid event timestamp')
                    # Do not persist the prompt body in the Poise journal or counter.
                    messages.append({'conversation_id':conversation,'message_id':str(start),
                        'occurred_at':timestamp,'reason':None,'subject':None})
                scanned+=len(line);offset=f.tell();last_start=start;last_digest=hashlib.sha256(line).hexdigest()
        return {'messages':messages,'cursor':{'offset':offset,'file_id':[stamp.st_dev,stamp.st_ino],
                    'anchor_start':last_start,'anchor_digest':last_digest},
                'complete_snapshot':offset==size,'snapshot_bytes':size,'scanned_bytes':scanned}


class RuntimeAdapter:
    """Composition adapter. The authoritative work goes through existing WorkTools."""
    def __init__(self,settings):
        self.settings=validate_settings(settings)

    def invoke(self,packet):
        from .clock import SystemClock
        exact_keys(packet,{'identity','capabilities','transcript','work'},'runtime packet')
        cfgpath=Path(self.settings['project_config']).resolve()
        root,cfg,_=load_config(cfgpath)
        identity=RuntimeIdentity.parse(cfg['project'],self.settings['adapter_id'],packet['identity'])
        inventory=validate_inventory(packet['capabilities'],[])
        caller=(CallerIdentity.native(cfg['project'],identity.key) if identity.kind=='external'
                else CallerIdentity.generated(cfg['project'],identity.key))
        established=establish_poise(cfgpath,caller,inventory,SystemClock())
        registry=established.registry;session=established.session.session_id
        h=established.runtime;self.runtime=h;work=deepcopy(packet['work'])
        from ..modules.work.domain import parse_request
        parse_request(work,cfg['batch'])
        source=None;cursor=None;path=None
        if packet['transcript'] is not None:
            exact_keys(packet['transcript'],{'path','initial_offset'},'transcript input')
            if cfg['batch']['message_source']!={'id':self.settings['adapter_id'],'mode':'runtime_event'}:
                raise PoiseError('Transcript requires explicit matching runtime_event source in project config')
            if work['messages']:raise PoiseError('Do not mix reported messages with the transcript stream')
            path=packet['transcript']['path'];cursor=registry.cursor(session,path)
            source=CodexTranscript(self.settings).read(path,packet['transcript']['initial_offset'],cursor,identity.external_session)
            work['messages']=source['messages']
        try:
            validate_inventory(inventory,self.settings['required_capabilities'])
        except PoiseError:
            current=h.current_task()
            h.interactions.record(h.interactions.prepare(work['messages']),session,
                current if current is not None and current['status'] not in ('completed','cancelled') else None)
            if source is not None:registry.save_cursor(session,path,cursor,source['cursor'])
            raise
        h.store.event(session,None,'adapter.bound',{'adapter':self.settings['adapter_id'],'capabilities':inventory})
        result=WorkTools(h).invoke(work)
        if source is not None:registry.save_cursor(session,path,cursor,source['cursor'])
        return {**result,'runtime_adapter':{'id':self.settings['adapter_id'],'session_id':session,
                    'capabilities':inventory,'inventory_observation':'explicit_manifest',
                    'transcript':None if source is None else {k:v for k,v in source.items() if k!='messages'},
                    'coverage':'partial' if source is not None else 'reported_or_unavailable'}}
