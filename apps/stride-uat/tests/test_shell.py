#!/usr/bin/env python3
"""Literal black-box verification for the standalone Stride UAT shell foundation."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; APP=ROOT/'apps'/'stride-uat'; PROVIDER=APP/'bin'/'stride-uat'; FIXTURE=APP/'fixtures'/'uat-config.json'; DOC=APP/'docs'/'shell-uat.md'
def compact(v): return json.dumps(v,ensure_ascii=False,separators=(',',':'))
def emit(errors,failures,n): print(compact({'errors':errors,'failures':failures,'tests_run':n})); return 1 if errors or failures else 0
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def fixture(): return json.loads(FIXTURE.read_text(encoding='utf-8'))
def write_config(root,mutate=None):
 root=Path(root); (root/'config').mkdir(parents=True,exist_ok=True); d=fixture(); mutate and mutate(d); p=root/'config'/'uat.json'; p.write_text(json.dumps(d,indent=2)+'\n'); return p
def run(config,packet=None,raw=None,cwd=None):
 if raw is None: raw=compact(packet)+'\n'
 env=os.environ.copy(); env['PYTHONDONTWRITEBYTECODE']='1'
 return subprocess.run([str(PROVIDER),'--config',str(config)],input=raw,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=str(cwd or ROOT),env=env,check=False)
def parsed(p): return json.loads(p.stdout)
def rejected(p,code,details):
 assert p.returncode==2,(p.returncode,p.stdout,p.stderr); assert p.stderr==''; b=parsed(p); assert b['schema']=='stride-uat/v1' and b['status']=='rejected'; assert b['error']['code']==code and b['error']['retryable'] is False and b['error']['details']==details,b

def inspect(run_id='missing-run'): return {'schema':'stride-uat/v1','operation':'inspect','input':{'run_id':run_id}}
def test_provider(): assert PROVIDER.is_file(),f'missing provider: {PROVIDER.relative_to(ROOT)}'; assert os.access(PROVIDER,os.X_OK)
def test_isolation():
 with tempfile.TemporaryDirectory(prefix='uat-shell-isolated-') as td:
  b=Path(td); c=write_config(b); (b/'inputs').mkdir(); foreign=b/'foreign'; foreign.mkdir(); sent=[foreign/n for n in ('requirements.sqlite','sequencing.sqlite','execution.sqlite','poise.sqlite')]
  for p in sent:p.write_bytes(b'private-state')
  poison=b/'config'/'processes'/'analysis.json'; poison.parent.mkdir(); poison.write_text('{bad'); before={str(p):sha(p) for p in [poison,*sent]}; p=run(c,inspect(),cwd=b); rejected(p,'not_found',{'run_id':'missing-run'}); assert {str(x):sha(x) for x in [poison,*sent]}==before
def test_two_configs():
 with tempfile.TemporaryDirectory(prefix='uat-shell-configs-') as td:
  b=Path(td); ca=write_config(b/'a'); cb=write_config(b/'b'); (b/'a'/'inputs').mkdir(); (b/'b'/'inputs').mkdir(); rejected(run(ca,inspect('a'),cwd=b),'not_found',{'run_id':'a'}); rejected(run(cb,inspect('b'),cwd=b),'not_found',{'run_id':'b'}); assert (b/'a'/'state').resolve()!=(b/'b'/'state').resolve()
def test_missing_required():
 with tempfile.TemporaryDirectory() as td:
  b=Path(td); c=write_config(b,lambda d:d.pop('state_root')); rejected(run(c,inspect(),cwd=b),'invalid_packet',{'path':'/config/state_root'}); assert not (b/'state').exists()
def test_unknown_config():
 with tempfile.TemporaryDirectory() as td:
  b=Path(td); c=write_config(b,lambda d:d.__setitem__('hidden_default',True)); rejected(run(c,inspect(),cwd=b),'invalid_packet',{'path':'/config'}); assert not (b/'state').exists()
def test_packet_limit():
 with tempfile.TemporaryDirectory() as td:
  b=Path(td); c=write_config(b,lambda d:d['limits'].__setitem__('packet_bytes',32)); rejected(run(c,raw='{"schema":"stride-uat/v1","operation":"inspect","input":{"run_id":"'+('x'*100)+'"}}\n',cwd=b),'limit_exceeded',{'limit':'packet_bytes'})
def test_invalid_json():
 with tempfile.TemporaryDirectory() as td:
  b=Path(td); c=write_config(b); rejected(run(c,raw='{bad\n',cwd=b),'invalid_json',{})
def test_unknown_operation():
 with tempfile.TemporaryDirectory() as td:
  b=Path(td); c=write_config(b); rejected(run(c,{'schema':'stride-uat/v1','operation':'unknown','input':{}},cwd=b),'invalid_packet',{'path':'/operation'})
BEHAVIOR=[('shell.provider',test_provider),('shell.isolation',test_isolation),('shell.config_roots',test_two_configs),('shell.missing_required',test_missing_required),('shell.unknown_config',test_unknown_config),('shell.packet_limit',test_packet_limit),('shell.invalid_json',test_invalid_json),('shell.operation',test_unknown_operation)]
def run_behavior():
 if not PROVIDER.is_file(): return emit([],[{'reason':'missing provider: apps/stride-uat/bin/stride-uat','test':'shell.provider'}],1)
 e=[]; f=[]
 for n,t in BEHAVIOR:
  try:t()
  except AssertionError as x:f.append({'reason':str(x) or 'assertion failed','test':n})
  except Exception as x:e.append({'reason':f'{type(x).__name__}: {x}','test':n})
 return emit(e,f,len(BEHAVIOR))
def run_docs():
 checks=[('docs.command',['apps/stride-uat/bin/stride-uat','--config']),('docs.config',['stride-uat-config/v1','state_root','workspace_root','input_roots','packet_bytes']),('docs.ownership',['Stride UAT','Requirements','Sequencing','Execution','private','state']),('docs.future',['T029','T030','apps/stride-uat/tests/test_shell.py','--suite behavior'])]
 e=[];f=[]
 try:text=DOC.read_text()
 except Exception as x:return emit([{'reason':f'{type(x).__name__}: {x}','test':'docs.file'}],[],0)
 for n,need in checks:
  miss=[x for x in need if x not in text]
  if miss:f.append({'reason':'missing literal(s):'+','.join(miss),'test':n})
 return emit(e,f,len(checks))
def main():
 p=argparse.ArgumentParser();p.add_argument('--suite',required=True,choices=('behavior','documentation'));a=p.parse_args(); return run_behavior() if a.suite=='behavior' else run_docs()
if __name__=='__main__': raise SystemExit(main())
