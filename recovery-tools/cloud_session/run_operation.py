#!/usr/bin/env python3
"""Execute an explicit bounded command/packet and retain its unabridged result."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('spec',type=Path);a=p.parse_args();s=json.loads(a.spec.read_text())
 out=Path(s['output']).resolve();out.parent.mkdir(parents=True,exist_ok=True)
 if out.with_suffix('.result.json').exists(): raise SystemExit('Operation evidence exists; inspect it instead of replaying blindly')
 argv=s['argv'];env=os.environ.copy();env.update(s.get('env',{}))
 input_text=None
 if s.get('input_file'): input_text=Path(s['input_file']).read_text()
 r=subprocess.run(argv,input=input_text,text=True,capture_output=True,cwd=s.get('cwd'),env=env,timeout=s.get('timeout_seconds',180))
 out.with_suffix('.stdout.txt').write_text(r.stdout);out.with_suffix('.stderr.txt').write_text(r.stderr)
 meta={'argv':argv,'input_file':s.get('input_file'),'exit_code':r.returncode,'stdout':str(out.with_suffix('.stdout.txt')),'stderr':str(out.with_suffix('.stderr.txt'))}
 try:
  data=json.loads(r.stdout); path=data.get('response_path')
  if path: data=json.loads(Path(path).read_text())
  out.with_suffix('.full.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');meta['full_response']=str(out.with_suffix('.full.json'))
 except (ValueError,TypeError,AttributeError): pass
 out.with_suffix('.result.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(meta,ensure_ascii=False,indent=2));raise SystemExit(r.returncode)
if __name__=='__main__':main()
