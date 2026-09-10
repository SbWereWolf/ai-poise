"""Atomic publication of reviewed Task/Sprint plans through their repositories."""
from copy import deepcopy
import json
from ..modules.catalogue.publication import DataPublication
from ..modules.actions.domain import ActionRun,PlanSpec
from ..modules.tasks.definition import validate_creation,build_task
from ..modules.sprints.domain import Sprint,SprintPlan,SprintPolicy
from ..modules.foundation.errors import DomainError
from ..modules.verification.domain import exact_keys


class PlanningPublications:
    def __init__(self,uow,project,processes,automatic_checks,sprint_policy,execution_hash,max_items):
        self.uow,self.project,self.processes=uow,project,deepcopy(processes)
        self.automatic_checks=deepcopy(automatic_checks)
        self.policy=SprintPolicy.parse(sprint_policy)
        self.execution_hash,self.max_items=execution_hash,max_items

    def publish(self,task_id,actor,intent):
        spec=DataPublication.parse(intent)
        with self.uow() as u:
            parent=u.tasks.load(task_id);parent._owned(actor)
            if parent.state.status!='active' or parent.route.node(parent.stage.stage_id).handler.value!='publish':
                raise DomainError('Data publication requires an active publication node')
            # The source section is not writable in this publication stage. It was
            # retained from the preceding accepted read-only inspection.
            names={r.name for r in parent.stage.rules}
            if spec.section in names or any(s.rule.name==spec.section and parent.stage.stage_id in s.write_stages for s in parent.content_policy.sections):
                raise DomainError('Publication source must not be writable in publication')
            section=parent.content_snapshot.section_map().get(spec.section)
            if section is None:raise DomainError('Reviewed source section is missing')
            try:body=json.loads(section.content)
            except (ValueError,TypeError) as exc:raise DomainError('Publication source must contain the exact structured plan') from exc
            plan=PlanSpec.parse({'kind':'data_publish','intent':spec.to_dict(),'body':body},self.max_items)
            stage,iteration=parent.stage.stage_id,parent.state.iteration
            previous=u.actions.load(task_id,stage,iteration)
            if previous is not None:
                run=ActionRun.restore(previous,self.max_items)
                if run.plan.digest!=plan.digest:raise DomainError('Reviewed publication intent changed')
                if run.status=='complete':return run
                raise DomainError('Incomplete data transaction cannot be silently repeated')
            sprint=None
            if spec.kind=='tasks':
                contracts=body
                if not isinstance(contracts,list) or not contracts or len(contracts)>self.max_items:
                    raise DomainError('Reviewed task publication must contain a bounded nonempty list')
                if any(not isinstance(c,dict) or c.get('sprint_id') is not None for c in contracts):
                    raise DomainError('Standalone batch must not bypass Sprint membership publication')
            else:
                exact_keys(body,{'sprint_id','template','changes'},'reviewed sprint plan')
                sprint=Sprint.draft(SprintPlan.create(body['sprint_id'],body['template'],self.policy),self.policy)
                sprint=sprint.revise(sprint.plan.apply(body['changes'],self.policy)).publish()
                contracts=sprint.plan.data['tasks']
                if u.sprints.get(body['sprint_id']) is not None or u.tasks.exists(body['sprint_id']):
                    raise DomainError('Publication cannot replace an existing sprint/task')
            prepared=[];ids=set()
            for contract in contracts:
                goal=contract.get('goal_type') if isinstance(contract,dict) else None
                if goal not in self.processes:raise DomainError('Unknown child goal type')
                metadata=validate_creation(contract,self.processes[goal],self.automatic_checks)
                ident=contract['id']
                if ident in ids or u.tasks.exists(ident) or u.sprints.get(ident) is not None:
                    raise DomainError('Child identity collides with existing work')
                if sprint is not None and ident==sprint.plan.data['id']:raise DomainError('Task/sprint ID collision')
                ids.add(ident)
                metadata.update(sprint_id=contract['sprint_id'],goal=contract['goal'],config_hash=self.execution_hash)
                prepared.append((build_task(metadata,None),metadata))
            # All candidates were validated before first write. Rows and receipt
            # share one UoW; no child is visible if any repository write fails.
            for child,metadata in prepared:u.tasks.create(child,metadata)
            if sprint is not None:
                record={'project':self.project,'actor':actor,'aggregate':sprint.to_dict(),
                        'processes':deepcopy(self.processes),'automatic_checks':deepcopy(self.automatic_checks),
                        'execution_hash':self.execution_hash}
                u.sprints.save(record,None);u.sprints.publish_members(record)
            run=ActionRun.new(plan)
            u.actions.create(task_id,stage,iteration,run.to_dict())
            next_run=run.start(0,{'source_section':spec.section,'parent_task':task_id})
            u.actions.save(task_id,stage,iteration,next_run.to_dict(),run.version);run=next_run
            next_run=run.record('ready',{'task_ids':sorted(ids),'sprint_id':None if sprint is None else sprint.plan.data['id'],
                                      'authorization':spec.authorization,'effect':'published'})
            u.actions.save(task_id,stage,iteration,next_run.to_dict(),run.version)
            return next_run
