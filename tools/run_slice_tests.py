#!/usr/bin/env python3
"""Run all collected tests in bounded, non-overlapping batches. No product mocks."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def run(root,output,batch_size,timeout):
    output.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root/'src'),str(root/'tests')))}
    collected=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q','tests'],cwd=root,env=env,
                             text=True,capture_output=True,timeout=timeout)
    (output/'collection.log').write_text(collected.stdout+collected.stderr)
    if collected.returncode:raise RuntimeError('Collection failed')
    ids=[line.strip() for line in collected.stdout.splitlines() if line.startswith('tests/') and '::' in line]
    if not ids or len(ids)!=len(set(ids)):raise RuntimeError('Invalid collection')
    (output/'test-ids.json').write_text(json.dumps(ids,ensure_ascii=False,indent=2))
    digests={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
             for base in ('src','tests','config','examples') for p in (root/base).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.json')}
    (output/'tested-files.json').write_text(json.dumps(digests,indent=2))
    batches=[]
    for i,start in enumerate(range(0,len(ids),batch_size)):
        subset=ids[start:start+batch_size];name=f'batch-{i:03d}';junit=output/(name+'.xml');log=output/(name+'.log')
        began=time.monotonic()
        with log.open('w') as stream:
            try:
                result=subprocess.run([sys.executable,'-m','pytest','-q','--disable-warnings',*subset,'--junitxml='+str(junit)],
                                      cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
                code=result.returncode
            except subprocess.TimeoutExpired:code=124
        cases=[]
        if junit.exists():
            for t in ET.parse(junit).iter('testcase'):
                cases.append({'name':t.attrib.get('name'),'failure':t.find('failure') is not None,
                              'error':t.find('error') is not None,'skipped':t.find('skipped') is not None})
        batches.append({'name':name,'ids':subset,'exit_code':code,'seconds':time.monotonic()-began,'cases':cases})
        (output/'progress.json').write_text(json.dumps({'collected':len(ids),'batches':batches},ensure_ascii=False,indent=2))
    after={k:hashlib.sha256((root/k).read_bytes()).hexdigest() for k in digests}
    passed=sum(not(c['failure'] or c['error'] or c['skipped']) for b in batches for c in b['cases'])
    ok=passed==len(ids) and all(b['exit_code']==0 for b in batches) and after==digests
    final={'status':'PASS' if ok else 'FAIL','collected':len(ids),'passed':passed,'source_unchanged':after==digests,
           'batch_size':batch_size,'timeout':timeout,'batches':batches}
    (output/'result.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
    return 0 if ok else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--batch-size',type=int,required=True)
    p.add_argument('--timeout',type=int,required=True);a=p.parse_args()
    if a.batch_size<=0 or a.timeout<=0:raise SystemExit('Positive explicit test budgets required')
    raise SystemExit(run(a.root.resolve(),a.output.resolve(),a.batch_size,a.timeout))
