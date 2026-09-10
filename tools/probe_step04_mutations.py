#!/usr/bin/env python3
"""Reproduce targeted negative checks in disposable source copies, never patch the project."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(root: Path, output: Path):
    cases=[
        ('historical_argument_dedup','src/harness/modules/evidence/domain.py',
         'if latest != value: proposals.append(value)','if value not in proposals: proposals.append(value)',
         'tests/evidence/test_domain.py::test_argument_A_B_A_selects_current_A_not_historical_B'),
        ('timeout_as_observation','src/harness/modules/evidence/domain.py',
         "transport_bad=not r['interpretable'] or r['timed_out'] or r['actual_exit_code'] is None or r['actual_exit_code'] < 0",
         'transport_bad=False','tests/evidence/test_domain.py::test_timeout_cannot_be_a_negative_subject_proof'),
        ('unsafe_publication_resume','src/harness/runtime.py',
         "and publication['execution_key']==execution_key and usable", 'and True',
         'tests/evidence/test_paths.py::test_push_resume_after_explicit_environment_change_rechecks'),
        ('unbound_environment','src/harness/runtime.py',
         "'invocations':invocations", "'invocations':[{'method':i['method'],'cwd':i['cwd']} for i in invocations]",
         'tests/evidence/test_paths.py::test_changed_observation_input_rejects_stale_argument'),
        ('io_in_domain','src/harness/modules/evidence/domain.py','import hashlib','import hashlib\nimport pathlib',
         'tests/evidence/test_architecture.py::test_evidence_domain_and_application_do_not_contain_io'),
    ]
    results=[]
    for name,rel,before,after,test in cases:
        with tempfile.TemporaryDirectory(prefix='harness-evidence-mutation-') as d:
            target=Path(d)
            for folder in ('src','tests','examples','config'):
                shutil.copytree(root/folder,target/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            shutil.copy2(root/'pyproject.toml',target/'pyproject.toml')
            path=target/rel;text=path.read_text()
            if text.count(before)!=1: raise RuntimeError(f'{name}: mutation target ambiguous')
            path.write_text(text.replace(before,after))
            proc=subprocess.run([sys.executable,'-m','pytest','-q',test],cwd=target,
                env={**os.environ,'PYTHONPATH':str(target/'src'),'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},
                capture_output=True,text=True,timeout=20)
            results.append({'name':name,'test':test,'exit_code':proc.returncode,
                            'detected':proc.returncode==1 and '1 failed' in proc.stdout,
                            'output_tail':(proc.stdout+proc.stderr)[-2400:]})
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'all_detected':all(r['detected'] for r in results),'cases':results},ensure_ascii=False,indent=2)+'\n')
    return all(r['detected'] for r in results)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    ok=run(Path(__file__).resolve().parents[1],args.output)
    print('All five mutations detected' if ok else 'Mutation check failed')
    raise SystemExit(0 if ok else 1)
