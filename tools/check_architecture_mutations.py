from pathlib import Path
import tempfile,shutil,subprocess,sys,json,os
if len(sys.argv) != 3:
 raise SystemExit('Usage: check_architecture_mutations.py SOURCE_ROOT OUTPUT_JSON')
R=Path(sys.argv[1]).resolve()
OUTPUT=Path(sys.argv[2]).resolve()
checks=[]
mutations=[('domain_io','src/harness/modules/tasks/domain.py','import sqlite3\n'),
           ('lifecycle_bypass','src/harness/runtime.py','\ndef bypass(data):\n    data["status"]="completed"\n'),
           ('sql_bypass','src/harness/runtime.py','\ndef bypass(db):\n    db.execute("UPDATE tasks SET status=\\\'completed\\\'")\n')]
for name,file,addition in mutations:
 with tempfile.TemporaryDirectory() as folder:
  p=Path(folder)
  shutil.copytree(R/'src',p/'src',ignore=shutil.ignore_patterns('__pycache__'))
  (p/'tests/ddd').mkdir(parents=True)
  shutil.copy2(R/'tests/ddd/test_architecture.py',p/'tests/ddd/test_architecture.py')
  target=p/file
  text=target.read_text()
  text=text.replace('from __future__ import annotations','from __future__ import annotations\n'+addition) if name=='domain_io' else text+addition
  target.write_text(text)
  result=subprocess.run([sys.executable,'-m','pytest','-q','tests/ddd/test_architecture.py'],cwd=p,
          env={**os.environ,'PYTHONPATH':str(p/'src')},text=True,capture_output=True,timeout=20)
  checks.append({'mutation':name,'exit_code':result.returncode,'detected':result.returncode==1 and '1 failed' in result.stdout,'output':result.stdout+result.stderr})
assert all(c['detected'] for c in checks),checks
OUTPUT.write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print([(c['mutation'],c['detected']) for c in checks])
