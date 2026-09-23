"""Build and install the two delivered packages offline, outside their source trees."""
import argparse, hashlib, json, os, shutil, subprocess, sys, venv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_evidence import source_tree, installed_tree


def prepare(source: Path, core: Path, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False); root.chmod(0o755)
    wheelhouse=root/'wheelhouse'; wheelhouse.mkdir()
    source_before=source_tree(source); core_before=source_tree(core)
    commands=[]
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME') and not k.startswith(('POISE_','CODEX_'))}
    def run(argv):
        r=subprocess.run(list(map(str,argv)),env=env,capture_output=True,text=True,timeout=120)
        commands.append({'argv':list(map(str,argv)),'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        (root/'prepare-log.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode: raise RuntimeError(r.stderr or r.stdout)
    for name,path in [('poise',source),('maintenance',core)]:
        copy=root/(name+'-source')
        shutil.copytree(path,copy,ignore=shutil.ignore_patterns('.git','build','dist','*.egg-info','__pycache__','.pytest_cache'))
        run([sys.executable,'-B','-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir',wheelhouse,copy])
    venv.EnvBuilder(with_pip=True).create(root/'venv')
    py=root/'venv/bin/python'
    run([py,'-m','pip','install','--no-index','--no-deps',*sorted(wheelhouse.glob('*.whl'))])
    run([py,'-I','-c','import poise,environment_maintenance;print(poise.__file__);print(environment_maintenance.__file__)'])
    if source_tree(source)!=source_before or source_tree(core)!=core_before:
        raise ValueError('Source changed while the delivery was being built')
    proof={'python':str(py),'source':str(source),'core':str(core),'offline':True,
           'source_fingerprint':source_tree(source), 'core_fingerprint':source_tree(core),
           'installed':installed_tree(py),
           'wheels':{f.name:hashlib.file_digest(f.open('rb'),'sha256').hexdigest() for f in wheelhouse.glob('*.whl')}}
    (root/'delivery.json').write_text(json.dumps(proof,indent=2)+'\n')
    return proof

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--core',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.source.resolve(),a.core.resolve(),a.output.resolve())))
