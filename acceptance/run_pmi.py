#!/usr/bin/env python3
"""PMI: environment, ordered PSI execution, bounded processes and durable protocol.

No application lifecycle is implemented here. PSI owns Arrange/Act/Assert.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_evidence import validate_delivery


def sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,path)


def validate(manifest):
    if manifest.get('schema')!='poise/pmi/v1':raise ValueError('Unsupported PMI schema')
    steps=manifest.get('steps')
    if not isinstance(steps,list) or not steps:raise ValueError('PMI requires nonempty steps')
    seen=set()
    for step in steps:
        ident=step.get('id','')
        if not re.fullmatch('[A-Za-z0-9_-]+',ident) or ident in seen:raise ValueError('Invalid/duplicate step ID')
        seen.add(ident)
        if not re.fullmatch('[a-z0-9_-]+',step.get('case','')):raise ValueError('Invalid PSI case')
        if type(step.get('timeout_seconds')) is not int or not 1<=step['timeout_seconds']<=3600:
            raise ValueError('Explicit bounded timeout is required')
    if not isinstance(manifest.get('profiles'),dict) or not manifest['profiles']:raise ValueError('Environment profiles required')
    return manifest


def environment_errors(profile,actual):
    errors=[]
    if actual['uid']==0:errors.append('PSI requires a non-root actor')
    if actual['os_id'] not in profile.get('os_ids',[actual['os_id']]):errors.append('OS does not match the selected PMI profile')
    if actual['os_version'] not in profile.get('versions',[actual['os_version']]):errors.append('OS version does not match')
    if actual['python']!=profile.get('python',actual['python']):errors.append('Python major/minor does not match')
    return errors


def validate_resume(old,new):
    if old.get('fingerprint')!=new.get('fingerprint'):raise ValueError('Resume inputs changed; start a new protocol')
    if any(s.get('status')=='RUNNING' for s in old.get('steps',[])):
        raise ValueError('Prior step has unknown external outcome; inspect it, do not replay blindly')
    if any(s.get('status')!='PASS' for s in old.get('steps',[])):
        raise ValueError('Failed or blocked PSI must be retained; start a new protocol after correction')


def execute_step(argv,workspace,timeout,env,case_id):
    workspace=Path(workspace); workspace.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();timed_out=False
    out_path=workspace/'pmi-stdout.log';err_path=workspace/'pmi-stderr.log'
    with out_path.open('wb') as out,err_path.open('wb') as err:
        p=subprocess.Popen(list(map(str,argv)),cwd=workspace,env=env or None,stdout=out,stderr=err,start_new_session=True)
        try:exit_code=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(p.pid,signal.SIGKILL);p.wait();exit_code=124
    result_path=workspace/'result.json'
    value={}
    try:value=json.loads(result_path.read_text())
    except (OSError,ValueError):pass
    verdict=('PASS' if exit_code==0 and value.get('status')=='PASS' else
             'BLOCKED' if exit_code==3 and value.get('status')=='BLOCKED' else 'FAIL')
    return {'id':case_id,'status':verdict,'exit_code':exit_code,'timed_out':timed_out,
            'seconds':round(time.monotonic()-started,3),'argv':list(map(str,argv)),
            'result':str(result_path),'result_sha256':sha256(result_path) if result_path.exists() else None,
            'stdout':str(out_path),'stderr':str(err_path),
            'stdout_sha256':sha256(out_path),'stderr_sha256':sha256(err_path)}


def main(argv=None):
    p=argparse.ArgumentParser(description='Запустить ПМИ: окружение, последовательность ПСИ и единый протокол.')
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--profile',default='ubuntu-24.04')
    p.add_argument('--python',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--workspace',type=Path,required=True);p.add_argument('--resume',action='store_true')
    p.add_argument('--limit',type=int,default=0,help='Maximum completed PSI cases in this invocation; 0 runs all')
    a=p.parse_args(argv)
    try:
        manifest=validate(json.loads(a.manifest.read_text()));profile=manifest['profiles'][a.profile]
        runner=Path(__file__).resolve().parent/'psi/cases.py'
        env={'PATH':str(a.python.parent)+':/usr/bin:/bin','HOME':str(a.workspace.resolve()),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
        observation=json.loads(subprocess.check_output([str(a.python),'-I','-c',
            'import json,os,platform,sys; r=platform.freedesktop_os_release();print(json.dumps({"os_id":r["ID"],"os_version":r["VERSION_ID"],"python":list(sys.version_info[:2]),"python_full":sys.version,"uid":os.geteuid(),"machine":platform.machine()}))'],env=env,text=True))
        commit=subprocess.check_output(['git','-c','safe.directory='+str(a.source.resolve()),'-C',str(a.source),'rev-parse','HEAD'],text=True).strip()
        dirty=bool(subprocess.check_output(['git','-c','safe.directory='+str(a.source.resolve()),'-C',str(a.source),'status','--porcelain'],text=True))
        inputs={'manifest_sha256':sha256(a.manifest),'profile':a.profile,'python':str(a.python.absolute()),
                'source':str(a.source.resolve()),'source_commit':commit,'source_dirty':dirty,
                'runner_sha256':sha256(Path(__file__)),
                'shared_evidence_sha256':sha256(Path(__file__).with_name('release_evidence.py')),
                'delivery':validate_delivery(a.python,a.source),
                'psi_sha256':{f.name:sha256(f) for f in runner.parent.glob('*.py')},'environment':observation}
        fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
        report={'schema':'poise/pmi-protocol/v1','pmi_id':manifest['id'],'fingerprint':fingerprint,
                'inputs':inputs,'status':'RUNNING','steps':[],'environment_errors':environment_errors(profile,observation)}
        a.workspace=a.workspace.resolve();protocol=a.workspace/'protocol.json'
        if a.resume:
            old=json.loads(protocol.read_text());validate_resume(old,report)
            for step in old.get('steps',[]):
                for key in ('result','stdout','stderr'):
                    if sha256(step[key])!=step[key+'_sha256']:
                        raise ValueError('Saved PSI evidence changed: '+step['id'])
            report=old
        else:a.workspace.mkdir(parents=True,exist_ok=False)
        if report['environment_errors']:
            report['status']='BLOCKED';save(protocol,report);print(json.dumps(report,ensure_ascii=False));return 3
        # The workspace is already prepared by PMI. PSI writes in it but does not reuse another case.
        seen={s['id'] for s in report['steps']}; executed=0
        for step in manifest['steps']:
            if step['id'] in seen:continue
            if a.limit and executed>=a.limit:break
            root=a.workspace/step['id'];root.mkdir()
            report['steps'].append({'id':step['id'],'status':'RUNNING'});save(protocol,report)
            cmd=[a.python,'-I',runner,'--case',step['case'],'--workspace',root,'--prepared-workspace',
                 '--python',a.python,'--source',a.source]
            outcome=execute_step(cmd,root,step['timeout_seconds'],env,step['id'])
            report['steps'][-1]=outcome;executed+=1
            report['status']=outcome['status'] if outcome['status']!='PASS' else 'RUNNING'
            save(protocol,report)
            if outcome['status']!='PASS':break
        if len(report['steps'])==len(manifest['steps']) and all(s['status']=='PASS' for s in report['steps']):report['status']='PASS'
        save(protocol,report)
        lines=['# Протокол ПМИ / ПСИ',f"Статус: **{report['status']}**",f"Профиль: `{a.profile}`",f"Коммит: `{commit}`",'', '| Испытание | Итог | Код выхода |','|---|---|---:|']
        lines += [f"| {s['id']} | {s['status']} | {s.get('exit_code','—')} |" for s in report['steps']]
        (a.workspace/'protocol.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps({'status':report['status'],'protocol':str(protocol),'completed':len(report['steps']),'total':len(manifest['steps'])}))
        return {'PASS':0,'FAIL':1,'BLOCKED':3,'RUNNING':3}[report['status']]
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as e:
        print(json.dumps({'status':'INVALID','error':str(e)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
