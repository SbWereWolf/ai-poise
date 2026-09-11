"""Isolated negative copies: tests must reject each deliberate regression."""
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();root=a.root.resolve();a.output.mkdir(parents=True,exist_ok=False)
    variants=[
      ('message_conflict','src/poise/modules/interactions/domain.py',
       'if m.identity in candidate and candidate[m.identity]!=m.data:',
       'if False:', 'tests/batch/test_work_tools.py::test_message_same_identity_different_body_rejected'),
      ('resume_sections','src/poise/infrastructure/sqlite/queries.py',
       "            return envelope", "            return json.loads(row['data'])",
       'tests/batch/test_work_tools.py::test_resume_restores_complete_candidate_without_result_file'),
      ('output_preflight','src/poise/interfaces/work.py',
       "if len(json.dumps(minimal,ensure_ascii=False,separators=(',',':'))+'\\n')>h.cfg['limits']['output_chars']:",
       'if False:', 'tests/batch/test_cli.py::test_small_output_cap_blocks_before_bootstrap_effect')]
    reports=[]
    for name,file,old,new,test in variants:
        with tempfile.TemporaryDirectory(prefix='poise-mutation-') as folder:
            dest=Path(folder)
            for d in ('src','tests','config','examples'):
                shutil.copytree(root/d,dest/d,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            shutil.copy2(root/'pyproject.toml',dest/'pyproject.toml')
            path=dest/file;s=path.read_text();assert s.count(old)==1,(name,old)
            path.write_text(s.replace(old,new))
            r=subprocess.run([sys.executable,'-m','pytest',test,'-q','--tb=short'],cwd=dest,
                env={**os.environ,'PYTHONPATH':str(dest/'src')},capture_output=True,text=True,timeout=40)
            (a.output/(name+'.log')).write_text(r.stdout+r.stderr)
            report={'name':name,'file':file,'test':test,'exit_code':r.returncode,'detected':r.returncode==1 and '1 failed' in r.stdout}
            reports.append(report);print(json.dumps(report),flush=True)
    (a.output/'result.json').write_text(json.dumps(reports,indent=2))
    return 0 if all(r['detected'] for r in reports) else 1
if __name__=='__main__':raise SystemExit(main())
