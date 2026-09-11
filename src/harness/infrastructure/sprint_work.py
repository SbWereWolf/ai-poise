"""Workspace readiness outside SQL locks; orchestration never mutates Task fields."""
from pathlib import Path
from ..application.sprints import SprintCommands
from ..modules.foundation.errors import HarnessError
from ..common import descendant


class SprintWork:
    def __init__(self,runtime):
        self.h=runtime
        self.commands=SprintCommands(runtime.store.unit_of_work,runtime.cfg['project'],runtime.session,
            runtime.cfg['sprint'],runtime.processes,runtime.cfg['automatic_checks'],runtime.config_hash)

    def known(self,sprint_id):return self.commands.known(sprint_id)

    def apply(self,packet):
        result=self.commands.apply(packet)
        # Receipt proves application exactly once; current context may have advanced.
        return self.overview(result['sprint'])

    def select(self,sprint_id):
        current=self.h.current_task()
        if current is None or current['status'] in ('completed','cancelled') or current['sprint_id']==sprint_id:
            self.commands.select(sprint_id)
        return self.overview(sprint_id)

    def query(self,sprint_id,view):
        if view=='current':return self.overview(sprint_id)
        result=self.commands.read(sprint_id,view)
        if result is None:raise HarnessError('No selected sprint')
        return result

    def overviews(self):
        return [self.overview(sprint_id) for sprint_id in self.commands.overview_ids()]

    def overview(self,sprint_id):
        out=self.commands.read(sprint_id,'current')
        if out is None:return None
        record=self.commands.read(out['sprint'],'plan')
        plan=record['aggregate']['plan'];waived={(x['predecessor'],x['successor']) for x in record['aggregate']['waivers']}
        facts={t['id']:t for t in out['tasks']};ready=[];bases={}
        for tid in out['eligible']:
            if tid in out['resumable']:
                # Resume the saved process/workspace; do not prepare a new baseline.
                ready.append(tid);bases[tid]=None;continue
            predecessors=[e['predecessor'] for e in plan['dependencies'] if e['successor']==tid and e['kind']=='result'
                          and (e['predecessor'],tid) not in waived]
            commits={facts[i]['result_commit'] for i in predecessors}
            if None in commits:
                out['blocked'].append({'task':tid,'reasons':[{'reason':'missing_predecessor_result','predecessors':predecessors}]});continue
            # Multiple different trees must be integrated deliberately, not guessed from ancestry.
            if len(commits)>1:
                out['blocked'].append({'task':tid,'reasons':[{'reason':'integration_required','predecessors':predecessors,'commits':sorted(commits)}]});continue
            base=next(iter(commits)) if commits else None
            if base is not None:
                try:self.h._git(Path(self.h.cfg['git']['repository']),'cat-file','-e',base+'^{commit}')
                except HarnessError:
                    out['blocked'].append({'task':tid,'reasons':[{'reason':'result_objects_unavailable','commit':base}]});continue
            ready.append(tid);bases[tid]=base
        out['eligible']=ready;out['start_revisions']=bases
        if out['status'] not in ('draft','completed','cancelled') and not ready and not out['active']:out['status']='blocked'
        current=self.h.current_task()
        out['active_task']=current['id'] if current is not None and current['status'] not in ('completed','cancelled') and current['sprint_id']==out['sprint'] else None
        out['sprint_root']=str(descendant(self.h.state,self.h.paths['sprints'])/out['sprint'])
        if out['status'] in ('completed','cancelled'):out['next_work']='Доложить результат; новой работы по спринту нет'
        elif not ready and not out['active'] and out['status']!='draft':out['next_work']='Разрешить указанные блокировки; задачи автоматически не выбирать'
        return out

    def start(self,task_id):
        h=self.h;record=h.task_queries.record(task_id)
        if record is None:raise HarnessError('Task does not exist')
        sid=record['sprint_id']
        if sid is not None and not self.known(sid):raise HarnessError('Task is not a published sprint member')
        current=h.current_task()
        if current is not None and current['status'] not in ('completed','cancelled') and current['id']!=task_id:
            raise HarnessError('Сначала завершить/передать текущую задачу')
        state=None if sid is None else self.overview(sid)
        if state is not None and task_id not in state['eligible']:raise HarnessError('Task is not eligible: '+str(state['blocked']))
        if record['config_hash']!=h.config_hash:raise HarnessError('Published task belongs to another execution configuration')
        execution=h.prepare_task_execution(task_id,None if state is None else state['start_revisions'][task_id])
        h.task_commands.start(task_id,h.session,execution)
        h.store.bind(h.session,task_id)
        if sid is not None:self.commands.select(sid)
        return h._context(h._task(),True)
