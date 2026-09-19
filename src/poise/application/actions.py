"""Application-owned durable action runs; no command execution under a UoW."""
import json

from ..modules.actions.domain import ActionRun, PlanSpec
from ..modules.foundation.errors import DomainError

class PlanCommands:
    def __init__(self,uow,max_steps):
        self.uow,self.max_steps=uow,max_steps

    def obtain(self,task_id,actor,stage,iteration,plan):
        with self.uow() as uow:
            task=uow.tasks.load(task_id);task._owned(actor)
            if task.stage.stage_id!=stage or task.state.iteration!=iteration or task.state.status!='active':
                raise DomainError('External action does not belong to current active iteration')
            saved=uow.actions.load(task_id,stage,iteration)
            if saved is None:
                run=ActionRun.new(plan);uow.actions.create(task_id,stage,iteration,run.to_dict());return run
            run=ActionRun.restore(saved,self.max_steps)
            if run.plan.digest!=plan.digest:
                raise DomainError('Started plan is immutable; request explicit rework instead')
            return run

    def update(self,task_id,actor,stage,iteration,old,new):
        with self.uow() as uow:
            task=uow.tasks.load(task_id);task._owned(actor)
            if task.stage.stage_id!=stage or task.state.iteration!=iteration or task.state.status!='active':
                raise DomainError('Action iteration changed')
            uow.actions.save(task_id,stage,iteration,new.to_dict(),old.version)
        return new

    def snapshot(self,task_id,stage,iteration):
        with self.uow() as uow:return uow.actions.load(task_id,stage,iteration)

    def publish_local(self, task_id, actor, stage, iteration, intent, candidate, tree, target):
        """Atomically record a local observation, without any Git publication effect."""
        plan = PlanSpec.parse({'kind': 'git_publish', 'intent': intent.to_dict(),
                               'candidate': candidate}, self.max_steps)
        with self.uow() as uow:
            task = uow.tasks.load(task_id)
            task._owned(actor)
            if (task.state.status != 'active' or task.stage.stage_id != stage
                    or task.state.iteration != iteration
                    or task.route.node(stage).handler.value != 'publish'):
                raise DomainError('Local publication requires the current publication iteration')
            if (task.state.submission_digest is None or task.progress.stage_work is None
                    or json.loads(task.progress.stage_work) != intent.to_dict()):
                raise DomainError('Local publication requires the submitted exact intent')
            execution, _ = uow.execution.load(task_id)
            report = execution['last_report']
            if (not report or report.get('status') != 'accepted'
                    or report.get('handler') != 'inspect' or report.get('stage_outcome') != 'clear'
                    or report.get('commit') != candidate or report.get('verified_tree') != tree
                    or execution['entry_tree'] != tree or execution['pending'] is not None):
                raise DomainError('Local publication requires the exact accepted inspection')
            saved = uow.actions.load(task_id, stage, iteration)
            if saved is not None:
                run = ActionRun.restore(saved, self.max_steps)
                if run.plan.digest != plan.digest:
                    raise DomainError('Started publication intent is immutable')
                if run.status not in ('complete', 'blocked'):
                    raise DomainError('Unresolved publication cannot become a local receipt')
                return run
            run = ActionRun.new(plan).start(0, {'target': target, 'candidate': candidate, 'tree': tree})
            receipt = {'effect': 'local_record_only', 'remote_publication': False,
                       'target_updated': False, 'candidate': candidate, 'tree': tree,
                       'target_ref': intent.target_ref, 'observed_target': target,
                       'authorization': intent.authorization}
            if target != intent.expected_commit:
                run = run.record('blocked', {**receipt, 'reason': 'target_changed'})
            else:
                run = run.record('ready', receipt)
            uow.actions.create(task_id, stage, iteration, run.to_dict())
            return run

    def record_assessment(self,task_id,actor,tree,receipt):
        with self.uow() as uow:
            task=uow.tasks.load(task_id)
            change=task.record_action_result(actor,tree,receipt)
            uow.tasks.save(change,task.state.version)

    def restart(self,task_id,actor,feedback,target,entry_tree, *, force_duplicate_start=False):
        with self.uow() as uow:
            task=uow.tasks.load(task_id)
            saved=uow.actions.load(task_id,task.stage.stage_id,task.state.iteration)
            if saved is None or saved['status'] not in ('failed','blocked'):
                raise DomainError('Only a known failed/blocked action permits this explicit restart')
            change=task.restart_action(actor,feedback,target)
            from .duplicate_tasks import require_duplicate_transition_in
            require_duplicate_transition_in(uow,task,change,actor,
                force_duplicate_start=force_duplicate_start)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{'entry_tree':entry_tree,'attempts':0,'publication':None,'pending':None})
