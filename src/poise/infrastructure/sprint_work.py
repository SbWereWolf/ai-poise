"""Workspace readiness outside SQL locks; orchestration never mutates Task fields."""
from ..application.sprints import SprintCommands
from ..modules.foundation.errors import PoiseError
from ..common import descendant


class SprintWork:
    def __init__(self,runtime):
        self.h=runtime
        self.commands=SprintCommands(runtime.store.unit_of_work,runtime.cfg['project'],runtime.session,
            runtime.cfg['sprint'],runtime.cfg.get('task_ids'),runtime.processes,
            runtime.cfg['automatic_checks'],runtime.cfg['task_decomposition'],
            runtime.config_hash,
            runtime.task_commands.prepare_creation,runtime._creation_base)

    def known(self,sprint_id):return self.commands.known(sprint_id)

    def apply(self,packet):
        preflight=None
        if isinstance(packet,dict) and packet.get('action') in ('cancel_tasks','cancel'):
            snapshot=self.commands.cancellation_snapshot(packet)
            if snapshot['receipt'] is not None:return snapshot['receipt']
            prepared={}
            for tid,item in snapshot['tasks'].items():
                run=self.h.cleanup_tools.prepare_terminal(tid,f"{packet['request_id']}:{tid}",packet['reason'])
                prepared[tid]={**item,'cleanup_pending':self.h.cleanup_tools.pending(run),
                               'cleanup_result':self.h.cleanup_tools.terminal_result(run)}
            preflight={**snapshot,'tasks':prepared}
        result=self.commands.apply(packet,preflight)
        # Receipt proves application exactly once; current context may have advanced.
        overview=self.overview(result['sprint'])
        return {**overview,**({'allocations':result['allocations']} if 'allocations' in result else {}),
                **({'materialized':result['materialized']} if 'materialized' in result else {}),
                **({'cleanup':result['cleanup']} if 'cleanup' in result else {})}

    def select(self,sprint_id):
        current=self.h.current_task()
        if current is None or current['status'] in ('completed','cancelled') or current['sprint_id']==sprint_id:
            self.commands.select(sprint_id)
        return self.overview(sprint_id)

    def query(self,sprint_id,view):
        if view=='current':return self.overview(sprint_id)
        result=self.commands.read(sprint_id,view)
        if result is None:raise PoiseError('No selected sprint')
        return result

    def overviews(self):
        return [self.overview(sprint_id) for sprint_id in self.commands.overview_ids()]

    def overview(self,sprint_id):
        out=self.commands.read(sprint_id,'current')
        if out is None:return None
        ready=out['eligible']
        current=self.h.current_task()
        out['active_task']=current['id'] if current is not None and current['status'] not in ('completed','cancelled') and current['sprint_id']==out['sprint'] else None
        out['sprint_root']=str(descendant(self.h.state,self.h.paths['sprints'])/out['sprint'])
        if out['status'] in ('completed','cancelled'):out['next_work']='Доложить результат; новой работы по спринту нет'
        elif not ready and not out['active'] and out['status']!='draft':out['next_work']='Разрешить указанные блокировки; задачи автоматически не выбирать'
        return out

    def start(self,task_id, *, force_duplicate_start=False):
        h=self.h;record=h.task_queries.record(task_id)
        if record is None:raise PoiseError('Task does not exist')
        sid=record['sprint_id']
        if sid is not None and not self.known(sid):raise PoiseError('Task is not a published sprint member')
        state=None if sid is None else self.overview(sid)
        if state is not None and task_id not in state['eligible']:raise PoiseError('Task is not eligible: '+str(state['blocked']))
        h.task_commands.duplicate_preflight(task_id,h.session,
            force_duplicate_start=force_duplicate_start)
        base=h._creation_base()
        execution=h._execution_reservation(task_id,base,record['process']['worktree_required'])
        h.task_commands.start(task_id,h.session,execution,force_duplicate_start=force_duplicate_start,
            restart_entry_tree=lambda retained: h._restart_entry_tree(task_id, retained))
        h._reconcile_task_worktree(h.task_queries.record(task_id))
        h.ownership.acquire_task(task_id,force_duplicate_start=force_duplicate_start)
        if sid is not None:self.commands.select(sid)
        return h._context(h._task(),True,force_duplicate_start=force_duplicate_start)
