"""Adapters for declared effects. A durable intent precedes every external mutation.

Only the current task worktree is changed. Long operations never hold a DB lock.
A Git checkpoint is explicitly UNVERIFIED and never publishes the target branch.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import re
import subprocess
import uuid
from ..application.actions import PlanCommands
from ..modules.actions.domain import parse_apply_work
from ..common import (
    PoiseError,
    descendant,
    digest,
    file_digest,
    prohibit_git_push,
    prohibit_remote_git_publication,
)
from ..execution import method_passed, run_command


class RuntimePlanActions:
    def __init__(self,poise):
        self.h=poise
        self.commands=PlanCommands(poise.store.unit_of_work,poise.cfg['batch']['max_items'])

    def snapshot(self,data):
        return self.commands.snapshot(data['id'],self.h._stage(data)['id'],data['iteration'])

    def _save(self,data,old,new):
        return self.commands.update(data['id'],self.h.session,self.h._stage(data)['id'],data['iteration'],old,new)

    def _obtain(self,data,plan):
        return self.commands.obtain(data['id'],self.h.session,self.h._stage(data)['id'],data['iteration'],plan)

    def validate(self,data,payload):
        kind=self.h._stage(data)['handler'];work=payload['stage_work']
        if kind=='apply_plan':
            plan=parse_apply_work(work,self.h.cfg['batch']['max_items'])
            saved=self.snapshot(data)
            if saved is not None and digest(saved['plan'])!=plan.digest:
                raise PoiseError('Started plan is immutable; explicit rework is required')
            if plan.kind=='git_merge':
                for source in plan.steps:
                    if re.fullmatch(self.h.cfg['git']['commit_pattern'],source['checkpoint_message']) is None:
                        raise PoiseError('Checkpoint message does not follow repository rules')
                if not re.fullmatch(self.h.cfg['git']['commit_pattern'],payload['commit_message']):
                    raise PoiseError('Verified integration commit needs its explicit message before effects')
        elif kind=='publish':
            if isinstance(work,dict) and 'kind' in work:
                from ..modules.catalogue.publication import DataPublication
                DataPublication.parse(work)
                return
            prohibit_remote_git_publication()

    def rework_failed(self,data,feedback,target,entry_tree):
        if self._read_optional_ref(Path(data['worktree']),'MERGE_HEAD') is not None:
            raise PoiseError('A pending merge must be resolved or explicitly aborted before restarting work')
        self.commands.restart(data['id'],self.h.session,feedback,target,entry_tree)

    def _command(self,data,argv,cwd,environment,timeout):
        root=descendant(self.h._roots(data)['task'],self.h.paths['runs'])/str(uuid.uuid4())
        result=run_command(argv,cwd,environment,timeout,
                           descendant(root,self.h.paths['stdout']),descendant(root,self.h.paths['stderr']))
        result={**result,'argv':argv,'cwd':str(cwd),
                'stdout_digest':file_digest(Path(result['stdout'])),
                'stderr_digest':file_digest(Path(result['stderr']))}
        self.h.store.event(self.h.session,data['id'],'action.command_finished',result)
        return result

    def _actor(self):
        cfg=self.h.cfg['git']
        return {**os.environ,'GIT_AUTHOR_NAME':cfg['author_name'],'GIT_AUTHOR_EMAIL':cfg['author_email'],
                'GIT_COMMITTER_NAME':cfg['author_name'],'GIT_COMMITTER_EMAIL':cfg['author_email']}

    def _git_effect(self,data,*args):
        wt=Path(data['worktree'])
        prohibit_git_push(['git', *args])
        return self._command(data,['git','-C',str(wt),*args],wt,self._actor(),self.h.cfg['limits']['git_seconds'])

    def _read_optional_ref(self,wt,ref):
        r=subprocess.run(['git','-C',str(wt),'rev-parse','--verify','--quiet',ref],capture_output=True,text=True,
                         timeout=self.h.cfg['limits']['git_seconds'])
        if r.returncode==1:return None
        if r.returncode:raise PoiseError('Git could not inspect reference')
        return r.stdout.strip()

    def _ancestor(self,wt,before,after):
        r=subprocess.run(['git','-C',str(wt),'merge-base','--is-ancestor',before,after],capture_output=True,
                         timeout=self.h.cfg['limits']['git_seconds'])
        if r.returncode not in (0,1):raise PoiseError('Git ancestry could not be established')
        return r.returncode==0

    def _conflicts(self,wt):
        raw=self.h._git(wt,'diff','--name-only','--diff-filter=U','-z')
        return [p for p in raw.split('\x00') if p]

    def _summary(self,run):
        snap=run.to_dict();current=snap['current']
        result={} if not snap['steps'] else snap['steps'][-1]['result']
        return {'status':snap['status'],'kind':run.plan.kind,'plan_digest':run.plan.digest,
                'cursor':run.cursor,'steps':snap['steps'],
                'conflicts':[] if current is None or 'conflicts' not in current else current['conflicts'],
                'reason':None if current is None or 'reason' not in current else current['reason'],
                'current':current,
                **({'allocations':result['allocations']} if 'allocations' in result else {})}

    def apply(self,data,payload):
        self.validate(data,payload)
        work=payload['stage_work'];plan=parse_apply_work(work,self.h.cfg['batch']['max_items'])
        run=self._obtain(data,plan)
        if plan.kind=='git_merge':
            # Recovery may finish one saved step. Continue the remaining known
            # mechanics in this call rather than asking the model to poll.
            for _ in range(len(plan.steps)+1):
                run=self._merge(data,run,work)
                if run.status!='prepared':break
        else:run=self._commands(data,run)
        return self._summary(run)

    def _merge(self,data,run,work):
        h=self.h;wt=Path(data['worktree'])
        # Validate the entire source set before the first mutation.
        for sha in [run.plan.data['base_commit']]+[s['commit'] for s in run.plan.steps]:
            if h._git(wt,'rev-parse','--verify',sha+'^{commit}')!=sha:
                raise PoiseError('Sources must identify exact existing commits')
        if run.status in ('failed','blocked','complete'):return run
        if run.status=='awaiting_resolution':
            current=json.loads(run.current)
            if work['phase']=='prepare':return run
            expected=set(current['conflicts']);provided={r['path'] for r in work['resolutions']}
            if provided!=expected:raise PoiseError('Provide one resolution for every observed conflict, and no unrelated paths')
            before=json.loads(run.before);source=run.plan.steps[run.cursor]['commit']
            if h._git(wt,'rev-parse','HEAD')!=before['head'] or self._read_optional_ref(wt,'MERGE_HEAD')!=source:
                raise PoiseError('The pending merge no longer belongs to this action')
            for path in expected:
                file=(wt/path)
                if not file.resolve().is_relative_to(wt.resolve()):raise PoiseError('Conflict path escapes the worktree')
                if file.is_file() and re.search(rb'(?m)^(?:<{7}|={7}|>{7})(?: |\r?$)',file.read_bytes()):
                    raise PoiseError('Unresolved conflict markers remain')
            h._git(wt,'add','--all','--',*sorted(expected))
            if self._conflicts(wt):raise PoiseError('The Git index still contains unresolved conflicts')
            run=self._finish_merge(data,run,{'resolutions':work['resolutions'],'conflicts':current['conflicts'],
                                            'merge_receipt':current['merge_receipt']})
        elif work['phase']=='continue' and run.status=='prepared' and run.cursor==0:
            raise PoiseError('No conflict observation exists for this continuation')
        # At most len(plan.steps) mechanical actions, bounded at parse time.
        while run.status=='prepared':
            head=h._git(wt,'rev-parse','HEAD')
            expected=run.plan.data['base_commit'] if run.cursor==0 else json.loads(run.steps[-1])['result']['head']
            if head!=expected or h._git(wt,'status','--porcelain') or self._read_optional_ref(wt,'MERGE_HEAD') is not None:
                raise PoiseError('Integration starts only from its exact clean owned state')
            run=self._save(data,run,run.start(run.cursor,{'head':head,'tree':h._tree(wt)}))
            source=run.plan.steps[run.cursor]['commit']
            receipt=self._git_effect(data,'merge','--no-ff','--no-commit','--no-edit','--no-stat',source)
            conflicts=self._conflicts(wt)
            if conflicts and self._read_optional_ref(wt,'MERGE_HEAD')==source:
                run=self._save(data,run,run.record('awaiting_resolution',{'conflicts':conflicts,'merge_receipt':receipt}))
            elif receipt['timed_out'] or receipt['actual_exit_code']!=0:
                run=self._save(data,run,run.record('blocked',{'reason':'merge_outcome_requires_inspection','merge_receipt':receipt}))
            else:run=self._finish_merge(data,run,{'conflicts':[],'resolutions':[],'merge_receipt':receipt})
        if run.status=='running':
            # Recovery probes only; no second merge while the first outcome is unknown.
            source=run.plan.steps[run.cursor]['commit'];before=json.loads(run.before)
            merge=self._read_optional_ref(wt,'MERGE_HEAD');head=h._git(wt,'rev-parse','HEAD')
            if merge==source and head==before['head']:
                conflicts=self._conflicts(wt)
                if conflicts:
                    run=self._save(data,run,run.record('awaiting_resolution',{'conflicts':conflicts,'merge_receipt':{'recovered':True}}))
                else:run=self._finish_merge(data,run,{'conflicts':[],'resolutions':[],'merge_receipt':{'recovered':True}})
            elif merge is None and head==before['head'] and self._ancestor(wt,source,head):
                run=self._finish_merge(data,run,{'conflicts':[],'resolutions':[],'merge_receipt':{'recovered':True,'no_op':True}})
            elif merge is None and run.cursor<len(run.plan.steps)-1 and h._git(wt,'show','-s','--format=%P',head).split()==[before['head'],source] and h._git(wt,'show','-s','--format=%B',head)==run.plan.steps[run.cursor]['checkpoint_message']:
                run=self._finish_merge(data,run,{'conflicts':[],'resolutions':[],'merge_receipt':{'recovered_checkpoint':True}})
            else:run=self._save(data,run,run.record('blocked',{'reason':'unknown_merge_outcome'}))
        return run

    def _finish_merge(self,data,run,details):
        h=self.h;wt=Path(data['worktree']);tree=h._tree(wt)
        checkpoint=None
        if run.cursor<len(run.plan.steps)-1 and self._read_optional_ref(wt,'MERGE_HEAD') is not None:
            checkpoint=self._git_effect(data,'commit','-m',run.plan.steps[run.cursor]['checkpoint_message'])
            if checkpoint['timed_out'] or checkpoint['actual_exit_code']!=0:
                return self._save(data,run,run.record('blocked',{'reason':'checkpoint_failed','receipt':checkpoint}))
            if h._tree(wt)!=tree or h._git(wt,'rev-parse','HEAD^{tree}')!=tree:
                h.store.event(h.session,data['id'],'incident.checkpoint_changed_tree',{})
                return self._save(data,run,run.record('blocked',{'reason':'checkpoint_changed_tree'}))
        return self._save(data,run,run.record('ready',{**details,'head':h._git(wt,'rev-parse','HEAD'),
                           'tree':tree,'checkpoint':checkpoint,'checkpoint_verified':False}))

    def _method(self,data,method):
        invocation=self.h._invocations([method],Path(data['worktree']))[0]
        receipt=self._command(data,method['argv'],Path(invocation['cwd']),invocation['environment'],None)
        receipt['method']=method['id']
        receipt['passed']=method_passed(method, receipt)
        return receipt

    def _commands(self,data,run):
        if run.status in ('failed','blocked','complete'):return run
        if run.status=='running':
            probe=self._method(data,run.plan.steps[run.cursor]['probe'])
            if probe['passed']:run=self._save(data,run,run.record('ready',{'probe':probe,'effect':'recovered_by_probe'}))
            else:return self._save(data,run,run.record('blocked',{'reason':'unknown_command_effect','probe':probe}))
        while run.status=='prepared':
            step=run.plan.steps[run.cursor]
            run=self._save(data,run,run.start(run.cursor,{'tree':self.h._tree(Path(data['worktree']))}))
            before=self._method(data,step['probe'])
            if before['passed']:
                run=self._save(data,run,run.record('ready',{'probe':before,'effect':'already_satisfied'}));continue
            if before['timed_out'] or before['actual_exit_code'] not in step['probe_false_exit_codes']:
                run=self._save(data,run,run.record('blocked',{'reason':'probe_not_interpretable','probe':before}));break
            applied=self._method(data,step['apply'])
            after=self._method(data,step['probe'])
            if applied['passed'] and after['passed']:
                run=self._save(data,run,run.record('ready',{'before_probe':before,'apply':applied,'probe':after,'effect':'applied'}))
            else:run=self._save(data,run,run.record('failed',{'reason':'desired_state_not_confirmed','before_probe':before,'apply':applied,'probe':after}))
        return run

    def publish(self,data,payload):
        self.validate(data,payload);h=self.h
        if 'kind' in payload['stage_work']:
            from ..application.planning_publication import PlanningPublications
            service=PlanningPublications(h.store.unit_of_work,h.cfg['project'],h.processes,
                h.cfg['automatic_checks'],h.cfg['sprint'],h.cfg.get('task_ids'),h.config_hash,
                h.cfg['batch']['max_items'],h.task_commands.prepare_creation,h._creation_base)
            return self._summary(service.publish(data['id'],h.session,payload['stage_work']))
        prohibit_remote_git_publication()
