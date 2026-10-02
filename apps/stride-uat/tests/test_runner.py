#!/usr/bin/env python3
"""Independent black-box contract tests for configurable UAT execution."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT/'apps/stride-uat'
FIX = APP/'fixtures/runner'
CLI = APP/'bin/stride-uat'
OBSERVATIONS = []


def fixture(name):
    return json.loads((FIX/name).read_text(encoding='utf-8'))


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


class Case:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uat-T029-')
        self.root = Path(self.temp.name)
        self.inputs = self.root/'inputs'; self.inputs.mkdir()
        self.owner = self.inputs/'owner.py'; shutil.copyfile(FIX/'owner.py', self.owner)
        self.program_path = self.inputs/'program.json'; shutil.copyfile(FIX/'program.json', self.program_path)
        self.program = fixture('program.json')
        self.config = fixture('config.json')
        release = platform.freedesktop_os_release()
        replacements = {'@OS_ID@':release['ID'], '@OS_VERSION@':release['VERSION_ID'],
                        '@MAJOR@':sys.version_info.major, '@MINOR@':sys.version_info.minor,
                        '@PYTHON@':sys.executable, '@OWNER@':str(self.owner),
                        '@PRODUCTION@':str(self.root/'production')}
        def substitute(v):
            if isinstance(v, str): return replacements.get(v,v)
            if isinstance(v, list): return [substitute(x) for x in v]
            if isinstance(v, dict): return {k:substitute(x) for k,x in v.items()}
            return v
        self.config = substitute(self.config)
        self.config_path = self.root/'uat.json'; write(self.config_path, self.config)
        self.state = self.root/'state'; self.workspace = self.root/'workspace'
        return self

    def __exit__(self, *args):
        self.temp.cleanup()

    def save(self):
        write(self.config_path,self.config); write(self.program_path,self.program)

    def mode(self, value):
        self.config['commands']['adapter']['argv'][3] = value
        self.save()

    def call(self, operation='execute', run_id='run-1', raw=None, outer=None):
        data = {'program_path':str(self.program_path),'profile_id':'ci'}
        if operation != 'validate': data['run_id'] = run_id
        packet = {'schema':'stride-uat/v1','operation':operation,'input':data}
        payload = raw if raw is not None else (compact(packet)+'\n').encode('utf-8')
        env = os.environ.copy(); env.update({'PYTHONDONTWRITEBYTECODE':'1','POISE_T029_AMBIENT_SECRET':'outer-only'})
        env.update(outer or {})
        proc = subprocess.run([str(CLI),'--config',str(self.config_path)],cwd=self.root,
                              env=env,input=payload,capture_output=True,timeout=30,check=False)
        OBSERVATIONS.append({'operation':operation,'request':payload.hex(),'exit_code':proc.returncode,
                             'stdout_hex':proc.stdout.hex(),'stderr_hex':proc.stderr.hex()})
        # A decoding/setup error remains an ERROR, not the expected RED assertion.
        value = json.loads(proc.stdout.decode('utf-8'))
        return proc,value

    def protocol(self, value):
        path = Path(value['protocol_path'])
        result = json.loads(path.read_text(encoding='utf-8'))
        OBSERVATIONS.append({'protocol_path':str(path),'protocol_sha256':digest(path),'protocol':result})
        return result

    def observation(self, step='first'):
        return json.loads((self.workspace/'run-1'/step/'owner-observation.json').read_text(encoding='utf-8'))


def reject(case, code, **kwargs):
    p,b = case.call(**kwargs)
    assert p.returncode == 2 and p.stderr == b'', (p.returncode,b,p.stderr)
    assert set(b) == {'schema','status','error'} and b['schema']=='stride-uat/v1' and b['status']=='rejected', b
    assert set(b['error'])==set(fixture('field-sets.json')['error']),b
    assert b['error']['code']==code and b['error']['retryable'] is False,b
    assert not case.state.exists() and not case.workspace.exists(), 'rejected input created run state'
    return b


def success(case, **kwargs):
    p,b = case.call(**kwargs)
    assert p.returncode == 0 and p.stderr == b'', (p.returncode,b,p.stderr)
    expected = fixture('executed.json'); expected['protocol_path']=str(case.state/'run-1/execution.json')
    assert b == expected,(b,expected)
    journal = case.protocol(b)
    assert set(journal)==set(fixture('field-sets.json')['protocol']),journal.keys()
    assert journal['schema']=='stride-uat-execution-protocol/v1'
    assert journal['qualification']=='not_evaluated' and 'verdict' not in journal
    assert journal['execution_status']=='COMPLETED' and journal['remaining_step_ids']==[]
    assert journal['stop_reason'] is None
    assert [s['step_id'] for s in journal['steps']]==['first','second']
    for s in journal['steps']:
        assert set(s)==set(fixture('field-sets.json')['step']),s.keys()
        assert s['state']=='EXITED' and s['exit_code']==0 and s['signal'] is None and not s['timed_out']
        assert s['effects']=='possible' and s['reason'] is None
        assert type(s['duration_seconds']) in (int,float) and s['duration_seconds']>=0
        for channel in ('stdout','stderr'):
            item=s[channel]; assert set(item)=={'path','bytes','sha256'}
            contents=Path(item['path']).read_bytes()
            assert item['bytes']==len(contents) and item['sha256']==hashlib.sha256(contents).hexdigest()
    return b,journal


def failed(case, reason, code=None):
    p,b=case.call(); assert p.returncode==1 and p.stderr==b'',(p.returncode,b,p.stderr)
    assert b['status']=='executed' and b['execution_status']=='FAILED' and b['qualification']=='not_evaluated',b
    assert b['stop_reason']==reason,b
    j=case.protocol(b); assert j['stop_reason']==reason and j['remaining_step_ids']==['second'],j
    assert len(j['steps'])==1 and not (case.workspace/'run-1/second').exists()
    step=j['steps'][0]
    if code is not None: assert step['exit_code']==code,step
    return b,j,step


def selected():
    with Case() as c:
        p,b=c.call()
        assert p.returncode==0 and b.get('execution_status')=='COMPLETED', (
            'runner.selected expected COMPLETED; observed exit='+str(p.returncode)+
            ',status='+str(b.get('status'))+',code='+str(b.get('error',{}).get('code')))
        assert p.stderr==b''
        expected=fixture('executed.json');expected['protocol_path']=str(c.state/'run-1/execution.json')
        assert b==expected,(b,expected)
        j=c.protocol(b);assert [s['application_id'] for s in j['steps']]==['widget-external','widget-external']
        assert j['qualification']=='not_evaluated' and j['execution_status']=='COMPLETED'
        assert c.observation()['public_stdout']=='subject-ok\n'


def validate():
    with Case() as c:
        p,b=c.call('validate'); expected=fixture('validated.json');expected['program_sha256']=digest(c.program_path)
        assert p.returncode==0 and p.stderr==b'' and b==expected,(p.returncode,b)
        assert not c.state.exists() and not c.workspace.exists()


def order():
    with Case() as c:
        _,j=success(c)
        for i,ident in enumerate(('first','second')):
            obs=c.observation(ident);s=j['steps'][i]
            assert obs['cwd']==str(c.workspace/'run-1'/ident)==s['cwd']
            assert s['argv']==c.config['commands']['adapter']['argv']+[obs['context_path']]
            assert obs['environment']['CASE_FLAG']=='declared' and obs['stdin']==''
            assert Path(s['stdout']['path']).read_bytes()==(ident+'\n').encode()
            assert Path(s['stderr']['path']).read_bytes()==('diag-'+ident+'\n').encode()


def configs():
    with Case() as c:
        c.config['state_root']='own-state';c.config['workspace_root']='own-work';c.config['profiles']['ci']['environment']['CASE_FLAG']='second-config';c.save()
        p,b=c.call();assert p.returncode==0
        j=c.protocol(b); assert b['protocol_path']==str(c.root/'own-state/run-1/execution.json')
        assert j['steps'][0]['cwd']==str(c.root/'own-work/run-1/first')
        obs=json.loads((c.root/'own-work/run-1/first/owner-observation.json').read_text())
        assert obs['environment']['CASE_FLAG']=='second-config' and not c.state.exists()


def fail_fast():
    with Case() as c:
        c.mode('fail');_,j,s=failed(c,'command_failed',7)
        assert s['state']=='EXITED' and s['effects']=='possible'
        assert json.loads(Path(c.observation()['context']['result_path']).read_text())=={'status':'PASS'}
        assert 'verdict' not in j


def stdout_limit():
    with Case() as c:
        c.config['limits']['stdout_bytes']=64;c.mode('stdout');_,_,s=failed(c,'output_limit')
        assert s['stdout']['bytes']<=64 and Path(s['stdout']['path']).read_bytes()==b'x'*64
        assert s['effects']=='possible'


def stderr_limit():
    with Case() as c:
        c.config['limits']['stderr_bytes']=64;c.mode('stderr');_,_,s=failed(c,'output_limit')
        assert s['stderr']['bytes']<=64 and Path(s['stderr']['path']).read_bytes()==b'e'*64


def timeout():
    for limit in ('step','total'):
        with Case() as c:
            if limit=='step': c.program['steps'][0]['timeout_seconds']=1
            else: c.config['limits']['total_timeout_seconds']=1
            c.mode('sleep');_,_,s=failed(c,'timeout');assert s['timed_out'] and s['effects']=='possible'
            assert (c.workspace/'run-1/first/started').is_file()


def signal():
    with Case() as c:
        c.mode('signal');_,_,s=failed(c,'signal',-15);assert s['signal']==15 and s['effects']=='possible'


def spawn():
    with Case() as c:
        c.config['commands']['adapter']['argv'][0]=str(c.inputs/'missing-executable');c.save()
        _,_,s=failed(c,'spawn_failed');assert s['exit_code'] is None and s['effects']=='none'


def invalid_json():
    for raw in (b'{bad',b'{"schema":1,"schema":2}',b'{"number":NaN}',b'{"value":"\xff"}',b'{} {}'):
        with Case() as c: reject(c,'invalid_json',raw=raw)
    with Case() as c:
        c.program_path.write_bytes(b'{"schema":1,"schema":2}')
        reject(c,'invalid_json')


def fields():
    for key,value in [('unknown',1),('version',''),('schema','unsupported')]:
        with Case() as c:
            c.program[key]=value;c.save();reject(c,'unsupported_schema' if key=='schema' else 'program_invalid')
    with Case() as c:
        del c.program['steps'][0]['graph_id'];c.save();reject(c,'program_invalid')
    with Case() as c:
        c.config['commands']['adapter']['extra']=True;c.save();reject(c,'invalid_packet')


def bindings():
    for change in ('duplicate','application','command','required_input','zero'):
        with Case() as c:
            if change=='duplicate':c.program['steps'][1]['id']='first'
            elif change=='application':c.program['steps'][0]['application_id']='unlisted'
            elif change=='command':c.config['commands']['adapter']['application_id']='unlisted'
            elif change=='required_input':c.config['commands']['adapter']['source_inputs']=['absent']
            else:c.program['steps']=[]
            c.save();reject(c,'program_invalid' if change!='required_input' else 'invalid_packet')


def limits():
    for target in ('steps','argv','argument','boolean','id'):
        with Case() as c:
            if target=='steps':c.config['limits']['steps']=1
            elif target=='argv':c.config['limits']['argv_items']=3
            elif target=='argument':c.config['limits']['argument_bytes']=8
            elif target=='boolean':c.program['steps'][0]['timeout_seconds']=True
            else:c.program['steps'][0]['id']='../escape'
            c.save();reject(c,'program_invalid' if target in ('boolean','id') else 'limit_exceeded')


def literal_argv():
    with Case() as c:
        marker=c.root/'MUST_NOT_EXIST'
        literal='$(touch '+str(marker)+'); echo injected'
        c.program['steps'][0]['arguments'].append(literal);c.save()
        success(c);assert c.observation()['arguments']==[literal] and not marker.exists()
    for literal in ('prefix${context}','${secret}'):
        with Case() as c:
            c.program['steps'][0]['arguments']=[literal];c.save();reject(c,'program_invalid')


def environment():
    with Case() as c:
        _,j=success(c);obs=c.observation()
        assert 'POISE_T029_AMBIENT_SECRET' not in obs['environment']
        assert set(obs['environment'])<=set(c.config['profiles']['ci']['environment'])|{'LC_CTYPE'}
        s=j['steps'][0];assert s['environment_keys']==['CASE_FLAG','LANG','SECRET_VALUE']
        expected=hashlib.sha256(json.dumps(c.config['profiles']['ci']['environment'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert s['environment_sha256']==expected and j['environment']['environment_sha256']==expected
        text=(c.state/'run-1/execution.json').read_text();assert 'test-only-token' not in text and 'outer-only' not in text
    with Case() as c:
        c.config['profiles']['ci']['environment']['BAD=KEY']='value';c.save();reject(c,'invalid_packet')


def file_limit():
    with Case() as c:
        c.config['limits']['program_bytes']=64;write(c.config_path,c.config);reject(c,'limit_exceeded')
    with Case() as c:
        c.owner.write_bytes(b'#'*(1048576+1));reject(c,'limit_exceeded')


def paths():
    with Case() as c:
        target=c.root/'outside.json';shutil.copyfile(c.program_path,target);c.program_path=target;reject(c,'input_invalid')
    with Case() as c:
        target=c.inputs/'target.json';c.program_path.rename(target);c.program_path.symlink_to(target);reject(c,'input_invalid')
    with Case() as c:
        target=c.root/'outside.py';c.owner.rename(target);c.owner.symlink_to(target);reject(c,'input_invalid')
    with Case() as c:
        c.config['workspace_root']='inputs/child';c.save();reject(c,'invalid_packet')


def conflict():
    with Case() as c:
        success(c);before={str(p):digest(p) for p in c.root.rglob('*') if p.is_file()}
        p,b=c.call();assert p.returncode==2 and b['error']['code']=='run_conflict'
        assert {str(p):digest(p) for p in c.root.rglob('*') if p.is_file()}==before
    with Case() as c:
        existing=c.workspace/'run-1';existing.mkdir(parents=True);(existing/'keep').write_bytes(b'old')
        p,b=c.call();assert p.returncode==2 and b['error']['code']=='run_conflict'
        assert (existing/'keep').read_bytes()==b'old' and not (existing/'first').exists()


def profile():
    with Case() as c:
        c.config['profiles']['ci']['os_ids']=['not-this-os'];c.save();reject(c,'environment_mismatch')
    with Case() as c:
        c.config['isolations']['trusted']['kind']='unavailable-launcher';c.save();reject(c,'capability_unavailable')
    with Case() as c:
        c.config['isolations']['trusted']['forbidden_roots']=[str(c.root)];c.save();reject(c,'invalid_packet')


def capability():
    with Case() as c:
        c.program['graphs']=[{'id':'graph','input_id':'owner-source','required_step_ids':['first']}];c.program['steps'][0]['graph_id']='graph';c.save();reject(c,'capability_unavailable')
    for op in ('run','resume'):
        with Case() as c:reject(c,'capability_unavailable',operation=op)


def context():
    with Case() as c:
        _,j=success(c);o=c.observation();x=o['context']
        assert set(x)==set(fixture('field-sets.json')['context'])
        assert x['schema']=='stride-uat-case-input/v1' and x['application_id']=='widget-external'
        assert x['run_id']=='run-1' and x['step_id']=='first' and x['graph'] is None
        assert x['workspace']==str(c.workspace/'run-1/first')
        assert x['result_path']==str(c.workspace/'run-1/first/result.json')
        assert x['public_argv']==c.config['applications']['widget-external']['public_argv']
        assert o['public_exit']==0 and o['public_stdout']=='subject-ok\n'
        assert 'verdict' not in j


def drift():
    with Case() as c:
        c.program['steps'][0]['arguments'].append(str(c.program_path));c.mode('drift')
        _,_,s=failed(c,'inputs_changed',0);assert s['state']=='EXITED'


def unicode():
    with Case() as c:
        alias=c.inputs/'питон';alias.symlink_to(sys.executable)
        c.config['commands']['adapter']['argv'][0]=str(alias)
        c.config['applications']['widget-external']['public_argv'][0]=str(alias)
        c.config['profiles']['ci']['environment']['CASE_FLAG']='значение'
        c.program['steps'][0]['arguments'].append('данные');c.save()
        _,j=success(c,outer={'PYTHONIOENCODING':'ascii'})
        assert j['steps'][0]['argv'][0]==str(alias)
        assert c.observation()['environment']['CASE_FLAG']=='значение'
        assert c.observation()['arguments']==['данные']


GROUPS = [('runner.'+n,f) for n,f in [
    ('selected',selected),('validate',validate),('order',order),('configs',configs),
    ('fail_fast',fail_fast),('stdout_limit',stdout_limit),('stderr_limit',stderr_limit),
    ('timeout',timeout),('signal',signal),('spawn',spawn),('json',invalid_json),
    ('fields',fields),('bindings',bindings),('limits',limits),('literal_argv',literal_argv),
    ('environment',environment),('file_limit',file_limit),('paths',paths),('conflict',conflict),
    ('profile',profile),('capability',capability),('context',context),('drift',drift),('unicode',unicode)]]


def documentation():
    path=APP/'docs/uat-runner.md'
    text=path.read_text(encoding='utf-8')
    checks=[('docs.command',['apps/stride-uat/bin/stride-uat','--config','execute','validate']),
            ('docs.config',['stride-uat-config/v1','stride-uat-program/v1','environment','source_inputs','forbidden_roots']),
            ('docs.protocol',['stride-uat-execution-protocol/v1','execution.json','not_evaluated','run_conflict','stdout_bytes','timeout']),
            ('docs.boundary',['T030','trusted-local','не является','PASS','test_runner.py','--suite behavior'])]
    failed_checks=[]
    for name,need in checks:
        missing=[s for s in need if s not in text]
        if missing: failed_checks.append({'reason':'missing literals: '+','.join(missing),'test':name})
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)',text):
        if '://' not in target and not (path.parent/target).is_file():
            failed_checks.append({'reason':'missing relative link: '+target,'test':'docs.boundary'})
    assert re.search('[А-Яа-яЁё]',text),'Russian human documentation required'
    return [],failed_checks,4


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--suite',required=True,choices=('red','behavior','documentation'));a=parser.parse_args()
    errors=[];failures=[];count=0
    if a.suite=='documentation':
        try:errors,failures,count=documentation()
        except Exception as exc:errors.append({'reason':type(exc).__name__+': '+str(exc),'test':'docs.file'})
    else:
        group=GROUPS[:1] if a.suite=='red' else GROUPS
        for name,fn in group:
            count+=1
            try:fn()
            except AssertionError as exc:failures.append({'reason':str(exc) or 'assertion failed','test':name})
            except Exception as exc:errors.append({'reason':type(exc).__name__+': '+str(exc),'test':name})
        output=os.environ.get('POISE_RUN_OUTPUT_DIR')
        if output:
            write(Path(output)/'uat-runner-observations.json',{'schema':'uat-runner-test-observations/v1','suite':a.suite,'observations':OBSERVATIONS})
    sys.stdout.buffer.write((compact({'errors':errors,'failures':failures,'tests_run':count})+'\n').encode('utf-8'))
    return 1 if errors or failures else 0


if __name__=='__main__':raise SystemExit(main())
