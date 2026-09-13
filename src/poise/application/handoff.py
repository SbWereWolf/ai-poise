"""Preserve/release and resume are domain commands, not SQL updates by a hook."""
from ..modules.foundation.errors import PoiseError


class HandoffCommands:
    def __init__(self,unit_of_work):self.uow=unit_of_work

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
            task=uow.tasks.load(task_id)
            if task.state.version!=version or task.state.claimed_by!=actor:
                raise PoiseError('Task changed before handoff preparation')
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
            task=uow.tasks.load(record['task_id'])
            if task.state.version!=record['version']:raise PoiseError('Task changed during handoff')
            uow.accounting_cycles.release(actor,record['task_id'])
            release_task_in(uow, actor, record['task_id'], record['plan']['reason'])
            uow.handoffs.replace({**record,'state':'released','receipt':receipt})

    def resume(self,task_id,actor,request_actor,request_id):
        with self.uow() as uow:
            record=uow.handoffs.get(request_actor,request_id)
            if record is None or record['state']!='released' or record['task_id']!=task_id:
                raise PoiseError('No released handoff for this task')
            task=uow.tasks.load(task_id)
            if task.state.version!=record['version']+1:raise PoiseError('Task changed after preserved handoff')
            uow.tasks.save(task.resume_handoff(actor),task.state.version)
            if uow.ownership.worktree_required(task_id):
                target=uow.ownership.preflight(actor,task_id)
                if target.worktree_owner not in (None,request_actor,actor):
                    raise PoiseError('Handoff worktree is owned by another session')
                if target.worktree_owner==request_actor:uow.ownership.bind_worktree(request_actor,None)
                uow.ownership.bind_worktree(actor,task_id)
            uow.handoffs.replace({**record,'state':'resumed'})
