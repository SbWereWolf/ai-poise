"""Application-owned durable action runs; no command execution under a UoW."""
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

    def record_assessment(self,task_id,actor,tree,receipt):
        with self.uow() as uow:
            task=uow.tasks.load(task_id)
            change=task.record_action_result(actor,tree,receipt)
            uow.tasks.save(change,task.state.version)

    def restart(self,task_id,actor,feedback,target,entry_tree):
        with self.uow() as uow:
            task=uow.tasks.load(task_id)
            saved=uow.actions.load(task_id,task.stage.stage_id,task.state.iteration)
            if saved is None or saved['status'] not in ('failed','blocked'):
                raise DomainError('Only a known failed/blocked action permits this explicit restart')
            change=task.restart_action(actor,feedback,target)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{'entry_tree':entry_tree,'attempts':0,'publication':None,'pending':None})
