"""Workspace readiness outside SQL locks; orchestration never mutates Task fields."""
from pathlib import Path
from ..application.sprints import SprintCommands
from ..modules.foundation.errors import PoiseError
from ..common import descendant


class SprintWork:
    def __init__(self,runtime):
        self.h=runtime
        self.commands=SprintCommands(runtime.store.unit_of_work,runtime.cfg['project'],runtime.session,
            runtime.cfg['sprint'],runtime.cfg.get('task_ids'),runtime.processes,
            runtime.cfg['automatic_checks'],runtime.config_hash,
            runtime.task_commands.prepare_creation,runtime._creation_base)

    def known(self,sprint_id):return self.commands.known(sprint_id)

    def apply(self,packet):
        preflight=None
        if isinstance(packet,dict) and packet.get('action')=='replace_task':
            snapshot=self.commands.replacement_snapshot(packet)
            if snapshot['receipt'] is not None:return snapshot['receipt']
            fact=snapshot['fact'];status=fact['status']
            if status in ('completed','cancelled','superseded'):
                raise PoiseError('Only an unfinished Task can be superseded')
            if fact['pending'] is not None:
                raise PoiseError('Task has a pending external outcome; resolve the pending operation before replacement')
            if status=='available':
                if fact['claimed_by'] is not None or fact['worktree'] is not None:
                    raise PoiseError('Available Task has ambiguous WIP; preserve it through handoff before replacement')
                safety={'kind':'available'}
            elif fact['claimed_by'] is not None:
                if fact['claimed_by']!=self.h.session:
                    raise PoiseError('Task is owned by another session; ask that owner to handoff before replacement')
                if fact['worktree'] is None:
                    raise PoiseError('Owned Task has ambiguous WIP; create a handoff before replacement')
                worktree=Path(fact['worktree'])
                if self.h._git(worktree,'status','--porcelain','--untracked-files=all'):
                    raise PoiseError('Task worktree is dirty; preserve WIP through handoff before replacement')
                safety={'kind':'caller_owned_clean','worktree':str(worktree),
                        'tree':self.h._git(worktree,'rev-parse','HEAD^{tree}')}
            else:
                handoff=snapshot['handoff']
                if handoff is None or handoff['state']!='released':
                    raise PoiseError('Unowned Task has ambiguous WIP; preserve it through an explicit handoff before replacement')
                safety={'kind':'released_handoff','handoff_request':handoff['request_id']}
            run=self.h.cleanup_tools.prepare_terminal(packet['source_task'],f"{packet['request_id']}:{packet['source_task']}",packet['authorization'])
            preflight={**snapshot,'safety':safety,'cleanup_pending':self.h.cleanup_tools.pending(run),
                       'cleanup_result':self.h.cleanup_tools.terminal_result(run)}
        elif isinstance(packet,dict) and packet.get('action') in ('cancel_tasks','cancel'):
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
        if isinstance(packet,dict) and packet.get('action')=='replace_task':return result
        overview=self.overview(result['sprint'])
        return {**overview,**({'allocations':result['allocations']} if 'allocations' in result else {}),
                **({'materialized':result['materialized']} if 'materialized' in result else {}),
                **({'cleanup':result['cleanup']} if 'cleanup' in result else {})}

    def select(self,sprint_id):
        current=self.h.current_task()
        if current is None or current['status'] in ('completed','cancelled','superseded') or current['sprint_id']==sprint_id:
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
        record=self.commands.read(out['sprint'],'plan')
        plan=record['aggregate']['plan'];waived={(x['predecessor'],x['successor']) for x in record['aggregate']['waivers']}
        facts={t['id']:t for t in out['tasks']};ready=[]
        result_predecessors={
            tid:[e['predecessor'] for e in plan['dependencies']
                 if e['successor']==tid and e['kind']=='result'
                 and (e['predecessor'],tid) not in waived]
            for tid in facts
        }
        out['result_provenance']={
            tid:[{'predecessor':predecessor,'result_commit':facts[predecessor]['result_commit']}
                 for predecessor in predecessors]
            for tid,predecessors in result_predecessors.items() if predecessors
        }
        for tid in out['eligible']:
            if tid in out['resumable']:
                # Resume the saved process/workspace; do not prepare a new baseline.
                ready.append(tid);continue
            predecessors=result_predecessors[tid]
            if any(facts[i]['result_commit'] is None for i in predecessors):
                out['blocked'].append({'task':tid,'reasons':[{'reason':'missing_predecessor_result','predecessors':predecessors}]});continue
            ready.append(tid)
        out['eligible']=ready
        if out['status'] not in ('draft','completed','cancelled') and not ready and not out['active']:out['status']='blocked'
        current=self.h.current_task()
        out['active_task']=current['id'] if current is not None and current['status'] not in ('completed','cancelled','superseded') and current['sprint_id']==out['sprint'] else None
        out['sprint_root']=str(descendant(self.h.state,self.h.paths['sprints'])/out['sprint'])
        if out['status'] in ('completed','cancelled'):out['next_work']='Доложить результат; новой работы по спринту нет'
        elif not ready and not out['active'] and out['status']!='draft':out['next_work']='Разрешить указанные блокировки; задачи автоматически не выбирать'
        return out

    def start(self,task_id):
        h=self.h;record=h.task_queries.record(task_id)
        if record is None:raise PoiseError('Task does not exist')
        sid=record['sprint_id']
        if sid is not None and not self.known(sid):raise PoiseError('Task is not a published sprint member')
        state=None if sid is None else self.overview(sid)
        if state is not None and task_id not in state['eligible']:raise PoiseError('Task is not eligible: '+str(state['blocked']))
        base=h._creation_base()
        execution=h._execution_reservation(task_id,base,record['process']['worktree_required'])
        h.task_commands.start(task_id,h.session,execution)
        h._reconcile_task_worktree(h.task_queries.record(task_id))
        h.ownership.acquire_task(task_id)
        if sid is not None:self.commands.select(sid)
        return h._context(h._task(),True)
