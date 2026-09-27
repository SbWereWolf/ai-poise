#!/usr/bin/env python3
"""Run an explicit public Poise packet in a parameterized recovery flow."""
import argparse,json,subprocess,sys
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--run',required=True,type=Path);p.add_argument('--name',required=True);p.add_argument('--packet',required=True,type=Path);a=p.parse_args();R=a.run.resolve();c=json.loads((R/'RUN.json').read_text());root=Path(c['destination']);op=root/'poise-op/operator'
 if Path(a.name).name!=a.name: raise SystemExit('Name must be a basename')
 spec={'argv':['bash',str(op/'poise.sh'),'work'],'cwd':str(root/'ai-poise'),'input_file':str(a.packet.resolve()),'env':{'POISE_CALLER_BINDING':str(Path(c['caller_binding']))},'output':str(R/'evidence'/a.name),'timeout_seconds':300}
 if c.get('bootstrap_environment',{}).get('git_identity_from_manifest'):
  manifest=json.loads((op/'profile/project/project.json').read_text());g=manifest['git']
  for key in ('author_name','author_email'):
   if not isinstance(g[key],str) or not g[key].strip(): raise SystemExit('Configured Git identity is absent')
  spec['env'].update({'GIT_AUTHOR_NAME':g['author_name'],'GIT_AUTHOR_EMAIL':g['author_email'],'GIT_COMMITTER_NAME':g['author_name'],'GIT_COMMITTER_EMAIL':g['author_email']})
 (R/'flows'/f'{a.name}.command.json').write_text(json.dumps(spec,indent=2)+'\n')
 flow={'schema':'workspace-recover/flow/v3','workspace':str(R/'workspaces'/a.name),'steps':[{'id':a.name,'op':'command','argv':[c['python'],str(R/'kit/run_operation.py'),str(R/'flows'/f'{a.name}.command.json')],'timeoutMs':330000,'onError':'stop'}]}
 manifest=R/'flows'/f'{a.name}.flow.json';manifest.write_text(json.dumps(flow,indent=2)+'\n')
 r=subprocess.run(['node',str(Path(c['workspace_recover_root'])/'bin/workspace-recover.mjs'),'flow','run','--manifest',str(manifest),'--session',str(R/'sessions'/a.name)],capture_output=True,text=True)
 (R/'evidence'/f'{a.name}.flow.json').write_text(r.stdout);(R/'evidence'/f'{a.name}.flow.stderr').write_text(r.stderr)
 try:
  f=json.loads((R/'evidence'/f'{a.name}.full.json').read_text());print(json.dumps({k:f.get(k) for k in ['status','stage','iteration','version','errors','error','next_stage','next_work']},ensure_ascii=False,indent=2))
 except (FileNotFoundError,ValueError): print(r.stdout, r.stderr)
 raise SystemExit(r.returncode)
if __name__=='__main__': main()
