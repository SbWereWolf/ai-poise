"""Atomic publication of reviewed Task/Sprint plans through their repositories."""
from copy import deepcopy
import json
from ..modules.catalogue.publication import DataPublication
from ..modules.actions.domain import ActionRun,PlanSpec
from ..modules.sprints.domain import Sprint,SprintPlan,SprintPolicy
from ..modules.foundation.errors import DomainError
from ..modules.verification.domain import exact_keys
from .tasks import (create_planned_in_uow, creation_batch_reservations, creation_intent_alias,
                    rewrite_task_plan, validate_creation_intent)


class PlanningPublications:
    def __init__(self,uow,project,processes,automatic_checks,sprint_policy,task_id_policy,
                 execution_hash,max_items,prepare_creation,creation_base):
        self.uow,self.project,self.processes=uow,project,deepcopy(processes)
        self.automatic_checks=deepcopy(automatic_checks)
        self.policy=SprintPolicy.parse(sprint_policy)
        self.task_id_policy=task_id_policy
        self.execution_hash,self.max_items=execution_hash,max_items
        self.prepare_creation=prepare_creation;self.creation_base=creation_base

    def publication_preflight(self,task_id,actor,spec):
        with self.uow() as u:
            parent=u.tasks.load(task_id);parent._owned(actor)
            if parent.state.status!='active' or parent.route.node(parent.stage.stage_id).handler.value!='publish':
                raise DomainError('Data publication requires an active publication node')
            names={r.name for r in parent.stage.rules}
            if spec.section in names or any(
                section.rule.name==spec.section
                and parent.stage.stage_id in section.write_stages
                for section in parent.content_policy.sections
            ):
                raise DomainError('Publication source must not be writable in publication')
            section=parent.content_snapshot.section_map().get(spec.section)
            if section is None:raise DomainError('Reviewed source section is missing')
            try:body=json.loads(section.content)
            except (ValueError,TypeError) as exc:
                raise DomainError('Publication source must contain the exact structured plan') from exc
            plan=PlanSpec.parse(
                {'kind':'data_publish','intent':spec.to_dict(),'body':body},self.max_items
            )
            stage,iteration=parent.stage.stage_id,parent.state.iteration
            previous=u.actions.load(task_id,stage,iteration)
            if previous is not None:
                run=ActionRun.restore(previous,self.max_items)
                if run.plan.digest!=plan.digest:
                    raise DomainError('Reviewed publication intent changed')
                if run.status=='complete':return {'run':run}
                raise DomainError('Incomplete data transaction cannot be silently repeated')
            if spec.kind=='tasks':
                contracts=body
                if not isinstance(contracts,list) or not contracts or len(contracts)>self.max_items:
                    raise DomainError('Reviewed task publication must contain a bounded nonempty list')
                if any(not isinstance(contract,dict) or contract.get('sprint_id') is not None for contract in contracts):
                    raise DomainError('Standalone batch must not bypass Sprint membership publication')
            else:
                exact_keys(body,{'sprint_id','template','changes'},'reviewed sprint plan')
                sprint=Sprint.draft(
                    SprintPlan.create(body['sprint_id'],body['template'],self.policy),self.policy
                )
                sprint=sprint.revise(sprint.plan.apply(body['changes'],self.policy)).publish()
                contracts=sprint.plan.data['tasks']
            snapshot={
                'version':parent.state.version,
                'stage':stage,
                'iteration':iteration,
                'section':section.content,
                'contracts':deepcopy(contracts),
            }
        base=self.creation_base()
        prepared=[];aliases=set()
        for intent in snapshot['contracts']:
            body_contract=intent.get('task') if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
            goal=body_contract.get('goal_type') if isinstance(body_contract,dict) else None
            if goal not in self.processes:raise DomainError('Unknown child goal type')
            candidate=self.prepare_creation(
                intent,self.processes[goal],self.automatic_checks,base
            )
            alias=creation_intent_alias(intent)
            if alias in aliases:raise DomainError('Duplicate child creation identity')
            aliases.add(alias);prepared.append(candidate)
        return {**snapshot,'prepared':prepared,'run':None}

    def publish(self,task_id,actor,intent):
        spec=DataPublication.parse(intent)
        preflight=self.publication_preflight(task_id,actor,spec)
        if preflight['run'] is not None:return preflight['run']
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
            if (parent.state.version!=preflight['version']
                    or parent.stage.stage_id!=preflight['stage']
                    or parent.state.iteration!=preflight['iteration']
                    or section.content!=preflight['section']):
                raise DomainError('Reviewed publication source changed after creation preflight')
            try:body=json.loads(section.content)
            except (ValueError,TypeError) as exc:raise DomainError('Publication source must contain the exact structured plan') from exc
            plan=PlanSpec.parse({'kind':'data_publish','intent':spec.to_dict(),'body':body},self.max_items)
            stage,iteration=parent.stage.stage_id,parent.state.iteration
            previous=u.actions.load(task_id,stage,iteration)
            if previous is not None:
                raise DomainError('Publication action changed after creation preflight')
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
            candidates=[];aliases=set()
            for intent,prepared in zip(contracts,preflight['prepared'],strict=True):
                body_contract=intent.get('task') if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
                goal=body_contract.get('goal_type') if isinstance(body_contract,dict) else None
                if goal not in self.processes:raise DomainError('Unknown child goal type')
                validate_creation_intent(intent,self.processes[goal],self.automatic_checks)
                alias=creation_intent_alias(intent)
                if alias in aliases:raise DomainError('Duplicate child creation identity')
                if prepared.source_intent!=intent:
                    raise DomainError('Reviewed child intent changed after creation preflight')
                aliases.add(alias);candidates.append((prepared,goal))
            reservations=creation_batch_reservations(
                contracts, () if sprint is None else (sprint.plan.data['id'],)
            )
            contracts_by_alias={};ids=set();allocations=[]
            for creation,goal in candidates:
                allocation,contract=create_planned_in_uow(
                    u,creation,{'config_hash':self.execution_hash},
                    self.task_id_policy,reservations)
                ident=contract['id']
                if ident in ids:
                    raise DomainError('Child identity collides with existing work')
                if sprint is not None and ident==sprint.plan.data['id']:raise DomainError('Task/sprint ID collision')
                ids.add(ident)
                alias=allocation.request_id if allocation.request_id is not None else ident
                contracts_by_alias[alias]=contract
                if allocation.receipt() is not None:allocations.append(allocation.receipt())
            if sprint is not None:
                from dataclasses import replace
                sprint=replace(
                    sprint,
                    plan=SprintPlan(rewrite_task_plan(sprint.plan.data,contracts_by_alias)),
                )
                record={'project':self.project,'actor':actor,'aggregate':sprint.to_dict(),
                        'processes':deepcopy(self.processes),'automatic_checks':deepcopy(self.automatic_checks),
                        'execution_hash':self.execution_hash}
                u.sprints.save(record,None);u.sprints.publish_members(record)
            run=ActionRun.new(plan)
            u.actions.create(task_id,stage,iteration,run.to_dict())
            next_run=run.start(0,{'source_section':spec.section,'parent_task':task_id})
            u.actions.save(task_id,stage,iteration,next_run.to_dict(),run.version);run=next_run
            next_run=run.record('ready',{'task_ids':sorted(ids),'allocations':allocations,
                                      'sprint_id':None if sprint is None else sprint.plan.data['id'],
                                      'authorization':spec.authorization,'effect':'published'})
            u.actions.save(task_id,stage,iteration,next_run.to_dict(),run.version)
            return next_run
