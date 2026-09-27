#!/usr/bin/env python3
"""Render a parameterized workspace-recover flow; transport stays with the host."""
import argparse,json
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--receipt',required=True,type=Path);p.add_argument('--run-config',required=True,type=Path);p.add_argument('--bridge',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--label',required=True);a=p.parse_args()
 r=json.loads(a.receipt.read_text());c=json.loads(a.run_config.read_text());d=c['delivery'];archive=Path(r['archive']).resolve();dest=a.output.parent.parent/'handoffs'/archive.stem
 body=('AI Poise cloud checkpoint — '+a.label+'\n\n'
       +'Snapshot: '+archive.name+'\nPrepared UTC: '+r['prepared_at_utc']+'\n'
       +'Archive bytes: '+str(r['bytes'])+'\nArchive SHA-256: '+r['sha256']+'\n'
       +'Task: '+str(r.get('task'))+'\nState and next step: '+str(r.get('note'))+'\nArtifact path: '+str(r.get('artifact_relative_path'))+'\n'
       +'Main commit: '+r['main_commit']+'\nTask commit: '+str(r.get('task_commit'))+'\n'
       +'Task artifact SHA-256: '+str(r.get('contract_sha256'))+'\n'
       +'Operator DB SHA-256: '+r['database_sha256']+'\n\n'
       +'Google Drive: ${outputs.upload.url}\n'
       +'Recovery: use the pinned workspace-recover 1.0.0; the immutable base d7890704cd40409cebeac0407d7ada39e0da5ce10ce8655e794731635be5be02 and this single cumulative checkpoint. The archive contains the parameterized recovery kit and evidence.\n'
       +'Cadence: batch every completed milestone and local commit into checkpoints 600–900 seconds apart; retry the same snapshot after a delivery failure. No Git push.\n'
       +'This storage handoff is not independent review, Task acceptance or release of a Task claim.\n')
 f={'schema':'workspace-recover/flow/v3','workspace':str(dest),'bridge':str(a.bridge.resolve()),'steps':[
 {'id':'upload','op':'host','operation':'drive.upload','payload':{'file':str(archive),'name':archive.name,'folderId':d['google_drive_parent'],'mimeType':'application/gzip'}},
 {'id':'download','op':'host','operation':'drive.download','payload':{'id':'${outputs.upload.id}'},'expected':{'sha256':r['sha256'],'bytes':r['bytes']}},
 {'id':'compose','op':'write','to':str(dest/'HANDOFF.txt'),'content':body},
 {'id':'send','op':'host','operation':'gmail.send','payload':{'to':d['gmail_recipient'],'subject':'AI Poise — CLOUD HANDOFF — '+a.label+' — '+r['main_commit'][:8],'bodyFile':'${outputs.compose.path}','attachments':[]}},
 {'id':'read-mail','op':'host','operation':'gmail.read','payload':{'id':'${outputs.send.id}'}}
 ]}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(f,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__': main()
