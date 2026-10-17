"""Offline non-editable installed consumer; genuine installed SDK events."""
from pathlib import Path
from copy import deepcopy
import json
import os
import shutil
import subprocess
import sys
import tomllib

from tests.content_reader.helpers import SOURCE, canonical, sha, strict_json, text_frames


NATIVE_CHILD = '''
import json, sys
from poise.infrastructure.hook_transport import HookService
p=json.load(sys.stdin); service=HookService(p['settings'])
if p['action']=='install':
 result=service.install(p['request'])
else:
 result=service.event(p['definition'],p['event'])
 binding=service.latest_binding(p['event']['session_id'],'primary')
 with service.registry.read_transaction() as db:
  row=db.execute('SELECT id FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',(binding['session_id'],p['event']['hook_event_name'])).fetchone()
 result={'receipt':result,'binding':binding,'event_id':None if row is None else row[0]}
print(json.dumps(result,ensure_ascii=False))
'''


def verify_installed(s, tmp_path, *, label="installed-reader"):
    """Return actual proof; one distinct owned build root per invocation."""
    assert s.proved
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / label
    root.mkdir()
    work = root / 'source'
    work.mkdir()
    # Build intermediates stay in this runner-owned copy, never the Task checkout.
    shutil.copytree(SOURCE, work / 'src')
    shutil.copy2(SOURCE.parent / 'pyproject.toml', work / 'pyproject.toml')
    env = {k: v for k, v in os.environ.items()
           if k not in {'PYTHONPATH', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID',
                        'POISE_CALLER_BINDING', 'POISE_SESSION', 'POISE_CONFIG'}}
    env.update(PIP_CACHE_DIR=str(root / 'pip-cache'), TMPDIR=str(root),
               PYTHONDONTWRITEBYTECODE='1')
    commands = []
    def run(argv, *, input=None, native=False):
        cp = subprocess.run(argv, input=input, capture_output=True, cwd=root,
                            env=env if not native else dict(env, CODEX_THREAD_ID=s.identity), check=False)
        commands.append({'argv':argv,'cwd':str(root),'exit_code':cp.returncode,
                         'stdout':cp.stdout.decode(),'stderr':cp.stderr.decode()})
        return cp
    def success(argv, **kwargs):
        cp = run(argv, **kwargs)
        assert cp.returncode == 0, commands[-1]
        return cp.stdout
    wheels = root / 'wheels'; wheels.mkdir()
    success([sys.executable,'-B','-m','pip','wheel','--no-deps','--no-build-isolation',
             '--no-index','--wheel-dir',str(wheels),str(work)])
    wheel, = wheels.glob('*.whl')
    venv = root / 'venv'
    success([sys.executable,'-B','-m','venv','--without-pip',str(venv)])
    python = str(venv / 'bin/python')
    success([sys.executable,'-B','-m','pip','--python',python,'install','--no-deps','--no-index',str(wheel)])
    provenance = strict_json(success([python,'-I','-B','-c',
        'import json,sys,poise,importlib.metadata as m;d=m.distribution("ai-poise");print(json.dumps({"prefix":sys.prefix,"module":poise.__file__,"version":d.version,"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(provenance['module']).is_relative_to(venv)
    declared = tomllib.loads((work/'pyproject.toml').read_text())['project']['version']
    assert provenance['version'] == declared
    direct = strict_json(provenance['direct_url'])
    assert not direct.get('dir_info',{}).get('editable',False)
    assert not Path(provenance['module']).is_relative_to(SOURCE)
    from tests.hook_transport.helpers import settings, definition, event
    selected = settings(s.base,tmp_path)
    config = json.loads(selected.read_text())
    config.update(python=python,source_root=str(Path(provenance['module']).parent.parent))
    selected.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    def sdk(action, **kwargs):
        return strict_json(success([python,'-I','-B','-c',NATIVE_CHILD],
                                  input=canonical(dict(action=action,settings=str(selected),**kwargs)).encode()))
    installation = sdk('install',request={'request_id':'installed-reader-hooks','expected_revision':None,'definition':definition()})
    s.native_identity('installed-reader-native')
    s.data['admission']={'hook_settings':str(selected),'definition_path':installation['definition_path']}
    s.save_policy()
    def observed(name):
        e=event(name,s.identity);e['cwd']=str(s.checkout)
        return sdk('event',definition=installation['definition_path'],event=e)
    start=observed('SessionStart')
    episode={'project_id':s.project,'session_id':s.identity,
             'binding_id':start['binding']['session_id'],'start_event_id':start['event_id']}
    s.episode=episode
    def reader(request,fmt='json'):
        cp=run([python,'-I','-B','-m','poise','content-read','--config',str(s.config),
                '--checkout',str(s.checkout),'--policy',str(s.policy),'--format',fmt],
               input=canonical(request).encode(),native=True)
        assert cp.returncode==0 and cp.stderr==b'',commands[-1]
        return cp.stdout
    template=strict_json(reader({'operation':'prepare_template'}))
    assert template['details']['schema_version']==2
    prepared=strict_json(reader({'operation':'prepare'}))
    assert prepared['details']['episode']==episode
    first=strict_json(reader(s.request()))
    s.row(first,text='βeta\nlast',selection='lines:2-3',classification='first_read')
    repeat=strict_json(reader(s.request()));assert repeat['items'][0]['status']=='unchanged'
    advance=strict_json(reader({'operation':'advance_context','request_id':'installed-text','reason':'manual'}))
    assert advance['context']['generation']==1
    frames=text_frames(reader(s.request(),'text').decode())
    assert frames[0][1]=='βeta\nlast'.encode()
    context=strict_json(reader({'operation':'context'}));assert context['context']['native_episode']==episode
    observed('SessionEnd')
    recovered=strict_json(reader({'operation':'recover'}))
    assert recovered['status']=='cleaned' and not Path(prepared['details']['path']).exists()
    commit=subprocess.check_output(['git','-C',str(SOURCE.parent),'rev-parse','HEAD'],text=True).strip()
    tree=subprocess.check_output(['git','-C',str(SOURCE.parent),'rev-parse','HEAD^{tree}'],text=True).strip()
    proof={'source_commit':commit,'source_tree':tree,'source_manifest_sha256':sha((work/'pyproject.toml').read_bytes()),
           'wheel':str(wheel),'wheel_sha256':sha(wheel.read_bytes()),'declared_version':declared,
           'installed':provenance,'commands':commands,'native_episode':episode,
           'consumers':{'template':template,'prepare':prepared,'first':first,'repeat':repeat,
                        'text_frames':[[h,b.decode()] for h,b in frames],'context':context,'recovery':recovered}}
    (Path(os.environ['POISE_RUN_OUTPUT_DIR'])/'reader.installed-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')

    return proof
