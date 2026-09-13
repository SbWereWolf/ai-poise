"""Local handoff in the same state store: preserve first, release ownership last.

No migration, remote store import or automatic backup delivery is implied.
"""
from copy import deepcopy
import json
import os
import re
import shutil
from pathlib import Path
from ..common import PoiseError,descendant,digest,file_digest,encoded
from ..application.handoff import HandoffCommands
from .goal_config import atomic_write


class LocalHandoff:
    def __init__(self,poise):
        self.h=poise;self.commands=HandoffCommands(poise.store.unit_of_work)
        self.config=poise.cfg['runtime_services']['handoff']

    def preserve(self,args):
        h=self.h
        request_id=h._identifier(args['request_id'])
        if not isinstance(args['reason'],str) or not args['reason'].strip():raise PoiseError('Handoff reason required')
        key=digest(args);prior=self.commands.lookup(h.session,request_id)
        if prior is not None:
            if prior['digest']!=key:raise PoiseError('Handoff request reused with different content')
            if prior['state'] in ('released','resumed'):
                return {**prior['receipt'],'replayed':True}
        data=h.current_task()
        if data is None:raise PoiseError('No current task to hand off')
        data=h._task();worktree_free=data['worktree'] is None
        worktree=h._verification_workspace(data)
        if data['pending'] is not None:raise PoiseError('Determine pending execution outcome before handoff')
        action=h.plan_actions.snapshot(data)
        if action is not None and action['status'] not in ('complete',):
            raise PoiseError('Finish or explicitly resolve the external plan before handoff')
        if not worktree_free and h.plan_actions._read_optional_ref(worktree,'MERGE_HEAD') is not None:
            raise PoiseError('Unfinished merge cannot be handed off by the local checkpoint adapter')
        tree=h._current_tree(data)
        if not worktree_free and h._git(worktree,'symbolic-ref','--short','HEAD')!=data['branch']:
            raise PoiseError('Worktree branch changed')
        verified=data['status'] in ('verified','accepted')
        if verified and tree!=data['last_report']['verified_tree']:
            raise PoiseError('Result changed after report: explicit rework before handoff')
        if not isinstance(args['artifact_paths'],list):raise PoiseError('artifact_paths list required')
        records=h.validate_artifact_paths(args['artifact_paths'],data)
        payload=args['result']
        if payload is None and not verified:
            payload=h.task_queries.latest_submission(data['id'],h._stage(data)['id'],data['iteration'])
        if payload is not None:
            if verified:raise PoiseError('Verified handoff does not accept a replacement result')
            h.validate_stage_result(data['id'],payload)
            records+=h.validate_artifact_paths(payload['artifact_paths'],data)
        head=data['base'] if worktree_free else h._git(worktree,'rev-parse','HEAD')
        dirty=False if worktree_free else h._git(worktree,'rev-parse','HEAD^{tree}')!=tree
        msg=args['commit_message']
        if dirty and (not isinstance(msg,str) or not re.fullmatch(h.cfg['git']['commit_pattern'],msg)):
            raise PoiseError('WIP requires explicit valid repository commit message')
        if not dirty and msg is not None and (not isinstance(msg,str) or not re.fullmatch(h.cfg['git']['commit_pattern'],msg)):
            raise PoiseError('Invalid supplied handoff commit message')
        directory=descendant(h._roots(data)['task'],self.config['directory'])/digest([h.session,request_id])
        # Every selected runtime artifact is copied to task before cleanup; data in
        # existing submissions remains immutable, receipt carries the mapping.
        directory.mkdir(parents=True,exist_ok=True)
        preserved=[];mapping={}
        for r in records:
            if r['scope']=='runtime':
                target=descendant(directory,self.config['preserved_directory'])/(r['digest']+'-'+Path(r['path']).name)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists() and file_digest(target)!=r['digest']:raise PoiseError('Preserved artifact changed')
                if not target.exists():
                    temp=target.with_name(target.name+'.pending');shutil.copyfile(r['path'],temp)
                    os.chmod(temp,self.config['file_mode']);os.replace(temp,target)
                if file_digest(target)!=r['digest']:raise PoiseError('Artifact changed during preservation')
                mapping[r['path']]=str(target);preserved.append(str(target))
            else:preserved.append(r['path'])
        if payload is not None:
            payload=deepcopy(payload)
            payload['artifact_paths']=[mapping.get(p,p) for p in payload['artifact_paths']]
            h.runner.submit(data['id'],h.session,payload)
            data=h._task()
        plan={'reason':args['reason'],'tree':tree,'head_at_start':head,'directory':str(directory),
              'verified':verified,'commit_message':msg,'preserved_artifacts':list(dict.fromkeys(preserved)),
              'artifact_mapping':mapping}
        if prior is None:
            prior=self.commands.prepare(h.session,request_id,key,data['id'],data['_version'],plan)
        else:
            plan=prior['plan']
            if tree!=plan['tree'] or data['_version']!=prior['version']:
                raise PoiseError('Preserved inputs changed during unfinished handoff')
        if not worktree_free and h._git(worktree,'rev-parse','HEAD^{tree}')!=plan['tree']:
            h._git(worktree,'add','--all')
            if h._git(worktree,'write-tree')!=plan['tree']:raise PoiseError('WIP index no longer matches captured state')
            actor={k:h.cfg['git'][v] for k,v in [('GIT_AUTHOR_NAME','author_name'),('GIT_COMMITTER_NAME','author_name'),
                                                    ('GIT_AUTHOR_EMAIL','author_email'),('GIT_COMMITTER_EMAIL','author_email')]}
            h._git(worktree,'commit','-m',plan['commit_message'],env={**os.environ,**actor})
        sha=plan['head_at_start'] if worktree_free else h._git(worktree,'rev-parse','HEAD')
        if (not worktree_free and
                (h._tree(worktree)!=plan['tree'] or h._git(worktree,'rev-parse','HEAD^{tree}')!=plan['tree'])):
            h.store.event(h.session,data['id'],'incident.handoff_tree_changed',{'expected_tree':plan['tree'],'commit':sha})
            raise PoiseError('Commit hook changed WIP tree; claim retained')
        directory=Path(plan['directory']);bundle=descendant(directory,self.config['bundle'])
        # Bundle is an offline copy, not a push and not proof of task completion.
        if not worktree_free and not bundle.exists():
            temp=bundle.with_name(bundle.name+'.pending')
            if temp.exists():temp.unlink()
            h._git(worktree,'bundle','create',str(temp),'HEAD')
            h._git(worktree,'bundle','verify',str(temp));os.replace(temp,bundle)
        if not worktree_free:
            heads=h._git(worktree,'bundle','list-heads',str(bundle))
            if not any(line.split()[0]==sha for line in heads.splitlines()):raise PoiseError('Bundle does not preserve current commit')
        receipt_path=descendant(directory,self.config['receipt'])
        receipt={'status':'handed_off','task':data['id'],'stage':h._stage(data)['id'],'iteration':data['iteration'],
                 'verified':plan['verified'],'commit':sha,'tree':plan['tree'],
                 'worktree':None if worktree_free else str(worktree),
                 'receipt_path':str(receipt_path),'bundle_path':None if worktree_free else str(bundle),
                 'bundle_digest':None if worktree_free else file_digest(bundle),
                 'preserved_artifacts':plan['preserved_artifacts'],'artifact_mapping':plan['artifact_mapping'],
                 'reason':plan['reason'],'replayed':False,'transfer_scope':'same_store_local_resume'}
        atomic_write(receipt_path,(encoded(receipt)+'\n').encode(),self.config['file_mode'])
        owned=[*plan['preserved_artifacts'],str(receipt_path)]
        if not worktree_free:owned.append(str(bundle))
        h.register_artifact_paths(owned,data)
        h.result_views.finish()
        # The legacy accounting cycle is a session lease: release it before
        # ownership so the same native session can immediately select another Task.
        h.accounting.release_cycle(data['id'])
        self.commands.release(h.session,request_id,receipt)
        h.store.event(h.session,data['id'],'handoff.released',{'receipt':str(receipt_path),'commit':sha,'verified':verified})
        h._cleanup_runtime()
        return receipt

    def resume(self,data):
        h=self.h;record=self.commands.latest(data['id'])
        if record is None:raise PoiseError('No explicit preserved handoff; cannot adopt unowned state')
        receipt=record['receipt']
        if data['worktree'] is None:
            if (receipt['worktree'] is not None or receipt['tree']!=data['entry_tree']
                    or receipt['commit']!=data['base'] or receipt['bundle_path'] is not None
                    or receipt['bundle_digest'] is not None):
                raise PoiseError('Worktree-free handoff facts changed')
        else:
            worktree=Path(data['worktree'])
            if (h._tree(worktree)!=receipt['tree'] or h._git(worktree,'rev-parse','HEAD')!=receipt['commit']
                or h._git(worktree,'symbolic-ref','--short','HEAD')!=data['branch']):
                raise PoiseError('Worktree changed since handoff; do not adopt external changes silently')
            if not Path(receipt['bundle_path']).is_file() or file_digest(Path(receipt['bundle_path']))!=receipt['bundle_digest']:
                raise PoiseError('Preserved source bundle missing or changed')
        self.commands.resume(data['id'],h.session,record['actor'],record['request_id'])
        h.store.event(h.session,data['id'],'handoff.resumed',{'from_session':record['actor'],'commit':receipt['commit']})
