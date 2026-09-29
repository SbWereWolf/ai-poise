#!/usr/bin/env python3
"""Standalone Stride UAT shell foundation owned by the Stride UAT app."""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path
SCHEMA='stride-uat/v1'; CONFIG_SCHEMA='stride-uat-config/v1'; ID_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
LIMITS={'packet_bytes','program_bytes','steps','id_bytes','argv_items','argument_bytes','result_bytes','stdout_bytes','stderr_bytes','step_timeout_seconds','lock_timeout_seconds','total_timeout_seconds'}
CONFIG_FIELDS={'schema','state_root','workspace_root','input_roots','limits','profiles','commands','applications','inputs','isolations'}
class Problem(Exception):
 def __init__(self,code,message,details): self.code=code; self.message=message; self.details=details

def pairs(pairs):
 d={}
 for k,v in pairs:
  if k in d: raise ValueError('duplicate key')
  d[k]=v
 return d

def loads(raw):
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))
def reject(code,message,details):
 print(json.dumps({'schema':SCHEMA,'status':'rejected','error':{'code':code,'message':message,'retryable':code=='store_busy','details':details}},separators=(',',':'))); return 2
def exact(obj,fields,path):
 if not isinstance(obj,dict): raise Problem('invalid_packet','Expected object',{'path':path})
 miss=sorted(fields-set(obj)); unknown=sorted(set(obj)-fields)
 if miss or unknown: raise Problem('invalid_packet','Object fields are invalid',{'path': path if unknown or len(miss)!=1 else path+'/'+miss[0]})
def posint(v,path):
 if isinstance(v,bool) or not isinstance(v,int) or v<=0: raise Problem('invalid_packet','Expected positive integer',{'path':path})
def resolve(base,v,path):
 if not isinstance(v,str) or not v: raise Problem('invalid_packet','Expected path string',{'path':path})
 p=Path(v); return (p if p.is_absolute() else (base/p)).resolve()
def load_config(path):
 try: raw=path.read_text(encoding='utf-8'); d=loads(raw)
 except Exception: raise Problem('invalid_packet','Invalid UAT configuration',{'path':'/config'})
 exact(d,CONFIG_FIELDS,'/config')
 if d['schema']!=CONFIG_SCHEMA: raise Problem('invalid_packet','Unsupported UAT configuration schema',{'path':'/config/schema'})
 base=path.parent.resolve(); state=resolve(base,d['state_root'],'/config/state_root'); workspace=resolve(base,d['workspace_root'],'/config/workspace_root')
 if state==workspace: raise Problem('invalid_packet','state_root and workspace_root must differ',{'path':'/config/workspace_root'})
 roots=d['input_roots']
 if not isinstance(roots,list) or not roots: raise Problem('invalid_packet','input_roots must be nonempty',{'path':'/config/input_roots'})
 input_roots=[resolve(base,x,f'/config/input_roots/{i}') for i,x in enumerate(roots)]
 exact(d['limits'],LIMITS,'/config/limits')
 for k in LIMITS: posint(d['limits'][k],f'/config/limits/{k}')
 for k in ('profiles','commands','applications','inputs','isolations'):
  if not isinstance(d[k],dict): raise Problem('invalid_packet','Expected object',{'path':f'/config/{k}'})
 return d,state,workspace,input_roots

def valid_id(v,limit,path):
 if not isinstance(v,str) or len(v.encode())>limit or not ID_RE.fullmatch(v): raise Problem('invalid_packet','Invalid identifier',{'path':path})
def main(argv=None):
 p=argparse.ArgumentParser(add_help=True); p.add_argument('--config',required=True); a=p.parse_args(argv)
 try: cfg,state,workspace,input_roots=load_config(Path(a.config))
 except Problem as e:return reject(e.code,e.message,e.details)
 limit=cfg['limits']['packet_bytes']; raw=sys.stdin.buffer.read(limit+1)
 if len(raw)>limit:return reject('limit_exceeded','Packet exceeds configured limit',{'limit':'packet_bytes'})
 try: packet=loads(raw.decode('utf-8'))
 except Exception:return reject('invalid_json','Invalid JSON',{})
 try:
  exact(packet,{'schema','operation','input'},'/');
  if packet['schema']!=SCHEMA: raise Problem('unsupported_schema','Unsupported schema',{'schema':packet.get('schema') if isinstance(packet.get('schema'),str) else None})
  op=packet['operation']; inp=packet['input']
  if op!='inspect': raise Problem('invalid_packet','Unsupported operation in T023 shell foundation',{'path':'/operation'})
  exact(inp,{'run_id'},'/input'); valid_id(inp['run_id'],cfg['limits']['id_bytes'],'/input/run_id')
  protocol=state/inp['run_id']/'protocol.json'
  if not protocol.is_file(): raise Problem('not_found','UAT run does not exist',{'run_id':inp['run_id']})
  # Full protocol readback/integrity is delivered by T030; reject existing material rather than infer PASS.
  raise Problem('store_incompatible','Existing UAT protocol requires the T030 protocol implementation',{'schema':None})
 except Problem as e:return reject(e.code,e.message,e.details)
if __name__=='__main__': raise SystemExit(main())
