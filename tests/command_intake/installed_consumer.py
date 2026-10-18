"""Offline wheel plus actual isolated public CLI and native SDK consumer."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from tests.command_intake.helpers import canonical, strict_json
from tests.conftest import write_json

SOURCE=Path(__file__).resolve().parents[2]/'src'
DRIVER=Path(__file__).with_name('installed_driver.py')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def installed(i, *, examples=None, label='installed-command'):
    output=Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    root=output/label;root.mkdir()
    work=root/'source';work.mkdir()
    shutil.copytree(SOURCE,work/'src')
    shutil.copy2(SOURCE.parent/'pyproject.toml',work/'pyproject.toml')
    env={k:v for k,v in os.environ.items() if k not in {
        'PYTHONPATH','CODEX_SESSION_ID','CODEX_THREAD_ID','POISE_CALLER_BINDING',
        'POISE_SESSION','POISE_CONFIG'}}
    env.update(PYTHONDONTWRITEBYTECODE='1',PIP_CACHE_DIR=str(root/'pip-cache'),TMPDIR=str(root))
    commands=[]
    def run(argv, *, input=None, native=False):
        selected=dict(env,CODEX_THREAD_ID=i.s.identity) if native else env
        cp=subprocess.run(argv,input=input,capture_output=True,cwd=root,env=selected,check=False)
        commands.append({'argv':argv,'cwd':str(root),'exit_code':cp.returncode,
                         'stdout':cp.stdout.decode(),'stderr':cp.stderr.decode()})
        assert cp.returncode==0,commands[-1]
        return cp.stdout
    wheels=root/'wheels';wheels.mkdir()
    run([sys.executable,'-B','-m','pip','wheel','--no-deps','--no-build-isolation',
         '--no-index','--wheel-dir',str(wheels),str(work)])
    wheel,=wheels.glob('*.whl')
    venv=root/'venv';run([sys.executable,'-B','-m','venv','--without-pip',str(venv)])
    python=str(venv/'bin/python')
    run([sys.executable,'-B','-m','pip','--python',python,'install','--no-deps','--no-index',str(wheel)])
    provenance=strict_json(run([python,'-I','-B','-c',
        'import json,sys,poise,importlib.metadata as m;d=m.distribution("ai-poise");print(json.dumps({"prefix":sys.prefix,"module":poise.__file__,"version":d.version,"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(provenance['module']).is_relative_to(venv)
    assert not Path(provenance['module']).is_relative_to(SOURCE)
    declared=tomllib.loads((work/'pyproject.toml').read_text())['project']['version']
    assert provenance['version']==declared
    assert not strict_json(provenance['direct_url']).get('dir_info',{}).get('editable',False)
    module_root=Path(provenance['module']).parent.parent
    # End the gate's real source episode before opening the installed episode.
    i.s.event('SessionEnd');assert i.s.invoke({'operation':'recover'})[0]==0
    settings_path=i.service.settings.path
    settings=deepcopy(i.settings)
    settings.update(python=python,source_root=str(module_root))
    profile=deepcopy(i.s.data)
    profile['session']['registry_template']=str(i.s.root/'installed-template.sqlite')
    policy=deepcopy(i.policy);definition=deepcopy(i.definition)
    if examples is not None:
        replacements={'@ROOT@':str(i.s.root),'@PYTHON@':python,
                      '@SOURCE_ROOT@':str(module_root),'@CHECKOUT@':str(i.s.checkout),
                      '@PROJECT_CONFIG@':str(i.s.config),'@READER_POLICY@':str(i.s.policy),
                      '@COMMAND_POLICY@':str(i.policy_path)}
        def fill(value):
            if isinstance(value,dict):return {k:fill(v) for k,v in value.items()}
            if isinstance(value,list):return [fill(v) for v in value]
            if isinstance(value,str):
                for token,replacement in replacements.items():value=value.replace(token,replacement)
            return value
        # All product-affecting settings come from the shipped examples.
        policy=fill(examples['policy']);profile=fill(examples['profile'])
        definition=fill(examples['definition']);settings=fill(examples['settings'])
        assert settings['python']==python and settings['source_root']==str(module_root)
        assert profile['command_intake']['policy']==str(i.policy_path)
        assert settings['tool_intake']['reader_policy']==str(i.s.policy)
    write_json(i.policy_path,policy);write_json(i.s.policy,profile);write_json(settings_path,settings)
    def sdk(action,**kwargs):
        return strict_json(run([python,'-I','-B',str(DRIVER)],
            input=canonical(dict(action=action,settings=str(settings_path),**kwargs)).encode(),native=True))
    revision=sdk('revision')['revision']
    installation=sdk('install',request={'request_id':'installed-command-hooks',
                                       'expected_revision':revision,'definition':definition})
    i.s.native_identity('installed-command-native')
    profile['admission']={'hook_settings':str(settings_path),'definition_path':installation['definition_path']}
    write_json(i.s.policy,profile)
    def event(name,**fields):
        value={'session_id':i.s.identity,'transcript_path':None,'cwd':str(i.s.checkout),
               'hook_event_name':name,**fields}
        if name=='SessionStart':value['source']='startup'
        if name=='SessionEnd':value['reason']='other'
        return sdk('event',definition=installation['definition_path'],event=value)
    start=event('SessionStart')
    assert start['event_id'] is not None
    def reader(request,*,sdk_port=False):
        if sdk_port:
            record=sdk('read',config=str(i.s.config),checkout=str(i.s.checkout),
                       policy=str(i.s.policy),request=request)
            assert record['exit_code']==0 and record['stderr']==''
            return strict_json(record['stdout'])
        return strict_json(run([python,'-I','-B','-m','poise','content-read',
            '--config',str(i.s.config),'--checkout',str(i.s.checkout),
            '--policy',str(i.s.policy),'--format','json'],input=canonical(request).encode(),native=True))
    template=reader({'operation':'prepare_template'})
    assert template['details']['schema_version']==2
    prepared=reader({'operation':'prepare'})
    resource=Path(prepared['details']['path'])
    direct=reader(i.s.request(path='docs/A.txt',start=1,end=2),sdk_port=True)
    assert direct['items'][0]['content']=='01 A\n02 A\n'
    assert direct['context']['generation']==0
    command=i.command("sed -n '3,4p' docs/A.txt")
    fields={'turn_id':'installed-turn','tool_name':'Bash','tool_use_id':'installed-ordinary',
            'tool_input':{'command':command}}
    pre=event('PreToolUse',**fields)
    assert pre['native'].get('hookSpecificOutput',{}).get('permissionDecision')!='deny'
    before=reader({'operation':'coverage'})
    assert before['details']['counts']['pending']==1
    assert before['details']['documents'][0]['ranges']==[[1,2]]
    cp=subprocess.run(['bash','-c',command],cwd=i.s.checkout,capture_output=True,text=True,check=False)
    commands.append({'argv':['bash','-c',command],'cwd':str(i.s.checkout),
                     'exit_code':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
    assert cp.returncode==0 and cp.stdout=='03 A\n04 A\n'
    post=event('PostToolUse',tool_response={'stdout':cp.stdout,'stderr':cp.stderr,'exit_code':cp.returncode},**fields)
    assert post['native'].get('continue') is not False
    after=reader({'operation':'coverage'},sdk_port=True)
    assert after['details']['counts']['pending']==0
    assert after['details']['counts']['current_documents']==1
    assert after['details']['documents'][0]['ranges']==[[1,4]]
    context=reader({'operation':'context'})
    assert context['context']['native_episode']['session_id']=='installed-command-native'
    assert context['context']['generation']==0
    event('SessionEnd')
    recovered=reader({'operation':'recover'})
    assert recovered['status']=='cleaned' and not resource.exists()
    commit=subprocess.check_output(['git','-C',str(SOURCE.parent),'rev-parse','HEAD'],text=True).strip()
    tree=subprocess.check_output(['git','-C',str(SOURCE.parent),'rev-parse','HEAD^{tree}'],text=True).strip()
    manifest={str(p.relative_to(work)):digest(p.read_bytes())
              for p in sorted((work/'src').rglob('*.py'))}
    proof={'source_commit':commit,'source_tree':tree,'source_manifest':manifest,
           'manifest_sha256':digest((work/'pyproject.toml').read_bytes()),
           'wheel':str(wheel),'wheel_sha256':digest(wheel.read_bytes()),
           'installed':provenance,'commands':commands,'template':template,'prepare':prepared,
           'direct':direct,'pre':pre,'post':post,'before':before,'after':after,
           'context':context,'recovery':recovered,'live_host_delivery':'not_observed'}
    if examples is not None:proof['examples']=examples
    name='installed.command-proof.json' if examples is None else 'command-intake.documented-examples.json'
    (output/name).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof
