"""Application-owned durable action runs; no command execution under a UoW."""
import json

from ..modules.actions.domain import ActionRun, PlanSpec
from ..modules.foundation.errors import DomainError
from ..modules.tasks.ports import TaskReadSet, TaskSourcesChanged

class PlanCommands:
    def __init__(self,uow,max_steps):
        self.uow,self.max_steps=uow,max_steps

    def obtain(self,task_id,actor,stage,iteration,plan):
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), action_task_ids=(task_id,)))
        task = inputs.task(task_id); task._owned(actor)
        if task.stage.stage_id!=stage or task.state.iteration!=iteration or task.state.status!='active':
            raise DomainError('External action does not belong to current active iteration')
        binding = inputs.action_binding(task_id, stage, iteration)
        saved = inputs.action_run(task_id, stage, iteration)
        if saved is not None:
            run = ActionRun.restore(saved, self.max_steps).bind(binding)
            if run.plan.digest != plan.digest:
                raise DomainError('Started plan is immutable; request explicit rework instead')
            return run
        run = ActionRun.new(plan).bind(binding)
        write = facade.prepare_action(inputs, task_id, stage, iteration, run.to_dict())
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.actions.create_prepared(write)
        return run

    def update(self,task_id,actor,stage,iteration,old,new):
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), action_task_ids=(task_id,)))
        task=inputs.task(task_id);task._owned(actor)
        if task.stage.stage_id!=stage or task.state.iteration!=iteration or task.state.status!='active':
            raise DomainError('Action iteration changed')
        binding=inputs.action_binding(task_id,stage,iteration)
        if old.binding is None or old.binding!=binding or new.binding!=old.binding:
            raise DomainError('Action lifecycle binding changed')
        if new.plan.digest!=old.plan.digest:
            raise DomainError('Started plan is immutable; request explicit rework instead')
        write = facade.prepare_action(inputs, task_id, stage, iteration, new.to_dict())
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.actions.save_prepared(write, old.version)
        return new

    def snapshot(self,task_id,stage,iteration):
        inputs = self.uow().capture(TaskReadSet((task_id,), action_task_ids=(task_id,)))
        return inputs.action_run(task_id,stage,iteration)

    def publish_local(self, task_id, actor, stage, iteration, intent, candidate, tree, target):
        """Atomically record a local observation, without any Git publication effect."""
        plan = PlanSpec.parse({'kind': 'git_publish', 'intent': intent.to_dict(),
                               'candidate': candidate}, self.max_steps)
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), action_task_ids=(task_id,), execution_ids=(task_id,)))
        task = inputs.task(task_id)
        task._owned(actor)
        if (task.state.status != 'active' or task.stage.stage_id != stage
                or task.state.iteration != iteration
                or task.route.node(stage).handler.value != 'publish'):
            raise DomainError('Local publication requires the current publication iteration')
        if (task.state.submission_digest is None or task.progress.stage_work is None
                or json.loads(task.progress.stage_work) != intent.to_dict()):
            raise DomainError('Local publication requires the submitted exact intent')
        execution, _ = inputs.required_execution(task_id)
        report = execution['last_report']
        if (not report or report.get('status') != 'accepted'
                or report.get('handler') != 'inspect' or report.get('stage_outcome') != 'clear'
                or report.get('commit') != candidate or report.get('verified_tree') != tree
                or execution['entry_tree'] != tree or execution['pending'] is not None):
            raise DomainError('Local publication requires the exact accepted inspection')
        binding = inputs.action_binding(task_id, stage, iteration)
        saved = inputs.action_run(task_id, stage, iteration)
        if saved is not None:
            run = ActionRun.restore(saved, self.max_steps).bind(binding)
            if run.plan.digest != plan.digest:
                raise DomainError('Started publication intent is immutable')
            if run.status not in ('complete', 'blocked'):
                raise DomainError('Unresolved publication cannot become a local receipt')
            return run
        run = ActionRun.new(plan).bind(binding).start(0, {'target': target, 'candidate': candidate, 'tree': tree})
        receipt = {'effect': 'local_record_only', 'remote_publication': False,
                   'target_updated': False, 'candidate': candidate, 'tree': tree,
                   'target_ref': intent.target_ref, 'observed_target': target,
                   'authorization': intent.authorization}
        if target != intent.expected_commit:
            run = run.record('blocked', {**receipt, 'reason': 'target_changed'})
        else:
            run = run.record('ready', receipt)
        write = facade.prepare_action(inputs, task_id, stage, iteration, run.to_dict())
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.actions.create_prepared(write)
        return run

    def record_assessment(self,task_id,actor,tree,receipt):
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), action_task_ids=(task_id,)))
        task=inputs.task(task_id)
        change=task.record_action_result(actor,tree,receipt)
        write = facade.prepare_change(inputs, change)
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)

    def restart(self,task_id,actor,feedback,target,entry_tree, *, force_duplicate_start=False):
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), action_task_ids=(task_id,), execution_ids=(task_id,), family_ids=(task_id,)))
        task=inputs.task(task_id)
        saved=inputs.action_run(task_id,task.stage.stage_id,task.state.iteration)
        if saved is None or saved['status'] not in ('failed','blocked'):
            raise DomainError('Only a known failed/blocked action permits this explicit restart')
        change=task.restart_action(actor,feedback,target)
        from .duplicate_tasks import require_duplicate_transition
        require_duplicate_transition(inputs,task,change,actor,
            force_duplicate_start=force_duplicate_start)
        write = facade.prepare_change(inputs, change)
        execution, version = inputs.required_execution(task_id)
        execution_write = facade.prepare_execution(inputs, task_id,
            {**execution, 'entry_tree':entry_tree, 'attempts':0, 'publication':None, 'pending':None})
        try:
            with self.uow() as uow:
                uow.require_inputs(inputs)
                uow.tasks.save_prepared(write)
                uow.execution.save_prepared(task_id, execution_write, version)
        except TaskSourcesChanged as conflict:
            require_duplicate_transition(conflict.current, task, change, actor,
                force_duplicate_start=force_duplicate_start)
            raise
