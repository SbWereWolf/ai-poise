#!/usr/bin/env python3
"""Run one cumulative snapshot through workspace-recover within the cloud window."""
import argparse,importlib.util,json,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path

def latest_receipt(directory: Path) -> dict | None:
    """Choose the latest actual preparation instant, independent of file names."""
    choices=[]
    for path in directory.glob('*-receipt.json'):
        data=json.loads(path.read_text())
        stamp=data.get('prepared_at_utc')
        if not isinstance(stamp,str): raise ValueError('Snapshot receipt lacks its preparation time')
        instant=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError('Snapshot receipt time must include a timezone')
        choices.append((instant,data))
    return max(choices,key=lambda row:row[0])[1] if choices else None

def main():
 p=argparse.ArgumentParser();p.add_argument('--run',required=True,type=Path);p.add_argument('--name',required=True);p.add_argument('--task',required=True);p.add_argument('--note',required=True);p.add_argument('--artifact');a=p.parse_args();R=a.run.resolve();c=json.loads((R/'RUN.json').read_text());root=Path(c['destination']);repo=root/'ai-poise'
 if Path(a.name).name!=a.name: raise SystemExit('Name must be a basename')
 previous=latest_receipt(R/'checkpoints')
 if previous is not None:
  spec=importlib.util.spec_from_file_location('cloud_cadence',repo/'recovery-tools/gmail_checkpoint.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  due=module.backup_due(previous,now=datetime.now(timezone.utc))
  if not due.get('eligible'): raise SystemExit(json.dumps(due))
  (R/'evidence'/f'{a.name}.cadence.json').write_text(json.dumps(due,indent=2)+'\n')
 for src in [R/'RUN.json',R/'capabilities.json']:
  if src.exists():shutil.copy2(src,R/'kit'/src.name)
 shutil.copytree(R/'flows',R/'kit/flows',dirs_exist_ok=True)
 if (R/'connector-results').exists():shutil.copytree(R/'connector-results',R/'evidence/connector-results',dirs_exist_ok=True)
 flow={'schema':'workspace-recover/flow/v3','workspace':str(R/'snapshot-workspaces'/a.name),'steps':[{'id':'snapshot','op':'command','argv':[c['python'],str(R/'kit/checkpoint.py'),a.name,'--task',a.task,'--note',a.note]+(['--artifact',a.artifact] if a.artifact else []),'env':{'POISE_RECOVERY_ROOT':str(root),'POISE_CHECKPOINT_OUTPUT':str(R/'checkpoints'),'POISE_BASE_MANIFEST':str(root/'.cloud-recovery/BASE-RECOVERY-MANIFEST.json'),'POISE_EVIDENCE_ROOTS':json.dumps([str(root/'.cloud-recovery/evidence'),str(R/'evidence')])},'timeoutMs':360000,'onError':'stop'}]}
 path=R/'flows'/f'{a.name}.snapshot.flow.json';path.write_text(json.dumps(flow,ensure_ascii=False,indent=2)+'\n')
 result=subprocess.run(['node',str(Path(c['workspace_recover_root'])/'bin/workspace-recover.mjs'),'flow','run','--manifest',str(path),'--session',str(R/'sessions'/a.name)],capture_output=True,text=True)
 (R/'evidence'/f'{a.name}.snapshot-status.json').write_text(result.stdout);(R/'evidence'/f'{a.name}.snapshot-stderr.txt').write_text(result.stderr)
 print(result.stdout);raise SystemExit(result.returncode)
if __name__=='__main__': main()
