"""Demonstrate regression sensitivity in disposable source copies; never edit input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    changes=[
        ('duplicate_hooks','src/harness/infrastructure/hook_transport.py',
         "previous=[] if old is None else json.loads(old[0])['groups']",'previous=[]',
         'tests/hook_transport/test_hooks.py::test_install_replay_and_edit_do_not_duplicate'),
        ('ignore_project_binding','src/harness/infrastructure/capabilities.py',
         "if p['json_assertions'] and not satisfies(strict_json(stdout),p['json_assertions']):",
         "if False and p['json_assertions'] and not satisfies(strict_json(stdout),p['json_assertions']):",
         'tests/hook_transport/test_probes.py::test_json_project_binding_is_verified_not_inferred'),
        ('double_count_user_turn','src/harness/infrastructure/hook_transport.py',
         "'message_id':native['turn_id']", "'message_id':native['turn_id']+__import__('uuid').uuid4().hex",
         'tests/hook_transport/test_demo.py::test_generated_native_hooks_and_bound_work_complete_task'),
    ]
    before={path:hashlib.sha256((root/path).read_bytes()).hexdigest() for _,path,*_ in changes};results=[]
    for name,path,old,new,test in changes:
        with tempfile.TemporaryDirectory(prefix='harness-hook-mutation-') as temp:
            copy=Path(temp)/'copy';copy.mkdir()
            for folder in ('src','tests','config','examples'):
                shutil.copytree(root/folder,copy/folder,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
            shutil.copy2(root/'pyproject.toml',copy/'pyproject.toml')
            f=copy/path;s=f.read_text();assert s.count(old)==1,(name,'mutation not precise');f.write_text(s.replace(old,new))
            args=[sys.executable,'-m','pytest',test,'-q','--tb=short']
            r=subprocess.run(args,cwd=copy,env={**os.environ,'PYTHONPATH':str(copy/'src')},capture_output=True,text=True,timeout=90)
            (out/(name+'.txt')).write_text(r.stdout+r.stderr)
            detected=r.returncode==1 and '1 failed' in r.stdout and ('AssertionError' in r.stdout or 'assert not True' in r.stdout)
            results.append({'id':name,'command':args,'exit_code':r.returncode,'detected':detected})
    after={path:hashlib.sha256((root/path).read_bytes()).hexdigest() for path in before}
    report={'source_unchanged':before==after,'mutations':results,'all_detected':all(r['detected'] for r in results)}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report))
    return 0 if report['all_detected'] and report['source_unchanged'] else 1

if __name__=='__main__':raise SystemExit(main())
