"""Preserve/release and resume are domain commands, not SQL updates by a hook."""
from ..modules.foundation.errors import PoiseError
from .check_attempts import validate_preserved_attempt_in


class HandoffCommands:
    def __init__(self,unit_of_work):self.uow=unit_of_work

    @staticmethod
    def _preservation_context_in(uow, task_id, actor):
        from ..modules.sprints.domain import Sprint
        if not uow.tasks.is_newborn(task_id):
            return None
        task = uow.tasks.load_newborn(task_id)
        if (task.claimed_by != actor or not task.ready or task.sprint_id is None
                or not any(item.get('from_status') in ('available', 'active', 'verified', 'accepted')
                           for item in task.restart_history)):
            return None
        record = uow.sprints.get(task.sprint_id)
        if record is None:
            return None
        sprint = Sprint.restore(record['aggregate'])
        if not sprint.preserves_readied_member(task_id):
            return None
        if not uow.execution.exists(task_id):
            return None
        execution, version = uow.execution.load(task_id)
        if (execution['worktree'] is None or execution['pending'] is not None
                or execution['publication'] is not None):
            return None
        if uow.ownership.snapshot(actor).worktree_task_id != task_id:
            return None
        return {'task_id': task_id, 'task_version': task.version,
                'sprint_id': task.sprint_id, 'sprint_revision': sprint.revision,
                'execution_version': version, 'worktree': execution['worktree'],
                'branch': execution['branch'], 'base': execution['base']}

    def preservation_context(self, task_id, actor):
        with self.uow() as uow:
            return self._preservation_context_in(uow, task_id, actor)

    def _require_preservation_in(self, uow, task_id, actor, plan):
        if 'wip_context' in plan and self._preservation_context_in(uow, task_id, actor) != plan['wip_context']:
            raise PoiseError('Task/Sprint preservation context changed; claims and WIP retained')

    def lookup(self,actor,request_id):
        with self.uow() as uow:return uow.handoffs.get(actor,request_id)

    def latest(self,task_id):
        with self.uow() as uow:return uow.handoffs.latest(task_id)

    def prepare(self,actor,request_id,digest,task_id,version,plan):
        with self.uow() as uow:
            old=uow.handoffs.get(actor,request_id)
            if old is not None:
                if old['digest']!=digest:raise PoiseError('Handoff request identity conflict')
                return old
            state=(uow.tasks.load_newborn(task_id) if uow.tasks.is_newborn(task_id)
                   else uow.tasks.load(task_id).state)
            if state.version!=version or state.claimed_by!=actor:
                raise PoiseError('Task changed before handoff preparation')
            self._require_preservation_in(uow, task_id, actor, plan)
            record={'actor':actor,'request_id':request_id,'digest':digest,'task_id':task_id,
                    'version':version,'state':'preparing','plan':plan,'receipt':None}
            uow.handoffs.insert(record)
            return record

    def release(self,actor,request_id,receipt):
        from .ownership import release_task_in
        with self.uow() as uow:
            record=uow.handoffs.get(actor,request_id)
            if record is None:raise PoiseError('No prepared handoff')
            if record['state']=='released':
                if record['receipt']!=receipt:raise PoiseError('Cannot replace a handoff receipt')
                return
            state=(uow.tasks.load_newborn(record['task_id']) if uow.tasks.is_newborn(record['task_id'])
                   else uow.tasks.load(record['task_id']).state)
            if state.version!=record['version']:raise PoiseError('Task changed during handoff')
            self._require_preservation_in(uow, record['task_id'], actor, record['plan'])
            recovery = record['plan'].get('uncertain_check_recovery')
            if recovery is not None:
                execution, _ = uow.execution.load(record['task_id'])
                if execution['pending'] != recovery['attempt']:
                    raise PoiseError('Uncertain attempt changed before handoff release')
                validate_preserved_attempt_in(uow, uow.tasks.load(record['task_id']),
                                              actor, execution['pending'])
            uow.accounting_cycles.release(actor,record['task_id'])
            release_task_in(uow, actor, record['task_id'], record['plan']['reason'])
            uow.handoffs.replace({**record,'state':'released','receipt':receipt})

    def resume(self,task_id,actor,request_actor,request_id, *, force_duplicate_start=False):
        with self.uow() as uow:
            record=uow.handoffs.get(request_actor,request_id)
            if record is None or record['state']!='released' or record['task_id']!=task_id:
                raise PoiseError('No released handoff for this task')
            if uow.tasks.is_newborn(task_id):
                newborn=uow.tasks.load_newborn(task_id)
                if newborn.version!=record['version']+1:
                    raise PoiseError('Task changed after preserved handoff')
                from .ownership import release_task_in
                before=uow.ownership.snapshot(actor)
                if before.task_id not in (None,task_id):
                    release_task_in(uow,actor,before.task_id)
                uow.tasks.acquire_newborn(task_id,actor)
            else:
                from .sprints import require_sprint_execution_in
                require_sprint_execution_in(uow, task_id)
                task=uow.tasks.load(task_id)
                from .duplicate_tasks import require_duplicate_start_in
                require_duplicate_start_in(uow,task_id,actor,force_duplicate_start=force_duplicate_start)
                from .tasks import require_reviewer_identity_in
                require_reviewer_identity_in(uow, task, actor, acquiring=True)
                if task.state.version!=record['version']+1:raise PoiseError('Task changed after preserved handoff')
                uow.tasks.save(task.resume_handoff(actor),task.state.version)
            if uow.ownership.worktree_required(task_id):
                target=uow.ownership.preflight(actor,task_id)
                if target.worktree_owner not in (None,request_actor,actor):
                    raise PoiseError('Handoff worktree is owned by another session')
                if target.worktree_owner==request_actor:uow.ownership.bind_worktree(request_actor,None)
                uow.ownership.bind_worktree(actor,task_id)
            uow.handoffs.replace({**record,'state':'resumed'})

    @staticmethod
    def resume_reuse_in(uow, task, actor, candidate, observed):
        """Consume an externally checked handoff with the reuse reservation UoW."""
        record = uow.handoffs.get(observed['actor'], observed['request_id'])
        if (record != observed or record['state'] != 'released'
                or record['task_id'] != task.state.task_id
                or task.state.claimed_by is not None
                or task.state.version != record['version'] + 1):
            raise PoiseError('Task changed after preserved handoff')
        change = task.prepare_duplicate_reuse(actor, candidate)
        uow.handoffs.replace({**record, 'state': 'resumed'})
        return change
