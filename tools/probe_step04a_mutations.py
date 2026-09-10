from pathlib import Path
import shutil,subprocess,os,json,tempfile
import argparse
parser = argparse.ArgumentParser(description="Reproduce four DDD-04A mutation probes on disposable source copies.")
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

checks=[
 ('stale_revision','src/harness/application/goal_config.py','if source_revision!=request["expected_revision"]:','if False:', 'tests/goal_config/test_service.py::test_stale_revision_and_reused_request_are_rejected'),
 ('conflicting_patch','src/harness/modules/goal_config/domain.py','if prior and ("*" in prior or "*" in fields or fields & prior):','if False:', 'tests/goal_config/test_domain.py::test_overlapping_intents_have_no_last_writer_wins'),
 ('wrong_pending_target','src/harness/infrastructure/goal_config.py','if json.loads(row["receipt"])["config_path"] != str(self.target):','if False:', 'tests/goal_config/test_service.py::test_pending_publication_cannot_be_redirected_by_new_settings'),
 ('snapshot_invalidated','src/harness/runtime.py','self.config_hash = digest(self.cfg)',"self.config_hash = digest({'config': self.cfg, 'processes': self.processes})",'tests/goal_config/test_runtime.py::test_existing_task_keeps_snapshot_and_new_task_gets_new_pack'),
]
results=[]
for name,file,old,new,node in checks:
 with tempfile.TemporaryDirectory(prefix='harness04a-mutation-') as d:
  p=Path(d)
  for dir in ('src','tests'): shutil.copytree(root/dir,p/dir,ignore=shutil.ignore_patterns('__pycache__'))
  shutil.copy2(root/'pyproject.toml',p/'pyproject.toml')
  target=p/file;s=target.read_text();assert old in s;target.write_text(s.replace(old,new,1))
  run=subprocess.run(['python','-m','pytest',node,'-q'],cwd=p,env={**os.environ,'PYTHONPATH':str(p/'src'),'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},text=True,capture_output=True,timeout=30)
  text=run.stdout+run.stderr
  log=output/f'mutation-{name}.txt';log.write_text(text)
  results.append({'name':name,'test':node,'exit_code':run.returncode,'detected':run.returncode==1 and 'FAILED' in text and 'ImportError' not in text,'log':log.name})
out=output/'mutation-checks.json';out.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2))

raise SystemExit(0 if all(r["detected"] for r in results) else 1)
