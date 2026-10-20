"""Atomic publication of reviewed Task/Sprint plans through their repositories."""
from copy import deepcopy
import json
from ..modules.catalogue.publication import DataPublication
from ..modules.actions.domain import ActionRun,PlanSpec
from ..modules.sprints.domain import Sprint,SprintPlan,SprintPolicy
from ..modules.foundation.errors import DomainError
from ..modules.tasks.ports import TaskReadSet
from ..modules.verification.domain import exact_keys
from .tasks import (creation_batch_reservations, creation_intent_alias, rewrite_task_plan)
from .task_preparation import compile_creation_batch


class PlanningPublications:
    def __init__(self,uow,project,workflows,automatic_checks,sprint_policy,task_id_policy,
                 decomposition_policy,execution_hash,max_items,prepare_creation,creation_base):
        self.uow,self.project,self.workflows=uow,project,deepcopy(workflows)
        self.automatic_checks=deepcopy(automatic_checks)
        self.decomposition_policy=deepcopy(decomposition_policy)
        self.policy=SprintPolicy.parse(sprint_policy)
        self.task_id_policy=task_id_policy
        self.execution_hash,self.max_items=execution_hash,max_items
        self.prepare_creation=prepare_creation;self.creation_base=creation_base

    def publication_preflight(self,task_id,actor,spec):
        inputs = self.uow().capture(TaskReadSet((task_id,), action_task_ids=(task_id,)))
        parent=inputs.task(task_id);parent._owned(actor)
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
        previous=inputs.action_run(task_id,stage,iteration)
        if previous is not None:
            run=ActionRun.restore(previous,self.max_items)
            if run.plan.digest!=plan.digest:
                raise DomainError('Reviewed publication intent changed')
            if run.status=='complete':return {'run':run}
            raise DomainError('Incomplete data transaction cannot be silently repeated')
        sprint = None
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
            'contracts':deepcopy(contracts), 'inputs': inputs, 'plan': plan, 'sprint': sprint,
        }
        base=self.creation_base()
        prepared=[];aliases=set()
        for intent in snapshot['contracts']:
            body_contract=intent.get('task') if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
            goal=body_contract.get('goal_type') if isinstance(body_contract,dict) else None
            if goal not in self.workflows:raise DomainError('Unknown child goal type')
            candidate=self.prepare_creation(
                intent,self.workflows[goal],self.automatic_checks,base,
                self.decomposition_policy,
            )
            alias=creation_intent_alias(intent)
            if alias in aliases:raise DomainError('Duplicate child creation identity')
            aliases.add(alias);prepared.append(candidate)
        return {**snapshot,'prepared':prepared,'run':None}

    def publish(self, task_id, actor, intent):
        from dataclasses import replace
        from ..modules.tasks.allocation import creation_parts
        spec = DataPublication.parse(intent)
        preflight = self.publication_preflight(task_id, actor, spec)
        if preflight['run'] is not None:
            return preflight['run']
        facade = self.uow()
        requests = tuple(request_id for prepared in preflight['prepared']
                         if (request_id := creation_parts(prepared.source_intent)[0]) is not None)
        sprint = preflight['sprint']
        sprint_id = None if sprint is None else sprint.plan.data['id']
        reservations = creation_batch_reservations(preflight['contracts'],
                                                   () if sprint_id is None else (sprint_id,))
        namespace_set = TaskReadSet((), allocation=True, creation_requests=requests)
        while True:
            namespace = facade.capture(namespace_set)
            writes = compile_creation_batch(facade, preflight['prepared'], namespace,
                {'config_hash': self.execution_hash}, self.task_id_policy, reservations)
            contracts = {write.allocation.request_id if write.allocation.request_id is not None
                         else write.allocation.task_id: write.contract for write in writes}
            allocations = [write.allocation.receipt() for write in writes
                           if write.allocation.request_id is not None]
            sprint_record = None
            if sprint is not None:
                published = replace(sprint, plan=SprintPlan(rewrite_task_plan(sprint.plan.data, contracts)))
                record = {'project': self.project, 'actor': actor, 'aggregate': published.to_dict(),
                    'workflows': deepcopy(self.workflows), 'automatic_checks': deepcopy(self.automatic_checks),
                    'task_decomposition': deepcopy(self.decomposition_policy), 'execution_hash': self.execution_hash}
                sprint_record = facade.prepare_sprint(record)
            initial = ActionRun.new(preflight['plan'])
            running = initial.start(0, {'source_section': spec.section, 'parent_task': task_id})
            complete = running.record('ready', {
                'task_ids': sorted(write.allocation.task_id for write in writes),
                'allocations': allocations, 'sprint_id': sprint_id,
                'authorization': spec.authorization, 'effect': 'published'})
            actions = [facade.prepare_action(preflight['inputs'], task_id, preflight['stage'],
                        preflight['iteration'], run.to_dict()) for run in (initial, running, complete)]
            with self.uow() as u:
                ready = all(u.tasks.require_creation_allocation(write.allocation, write.receipt)
                            for write in writes)
                if ready:
                    u.require_inputs(preflight['inputs'])
                    if sprint_id is not None:
                        u.tasks.require_unused_identity(sprint_id)
                    for write in writes:
                        write.save(u)
                    if sprint_record is not None:
                        u.sprints.save_prepared(sprint_record, None)
                        u.sprints.publish_prepared_members(sprint_record)
                    u.actions.create_prepared(actions[0])
                    u.actions.save_prepared(actions[1], initial.version)
                    u.actions.save_prepared(actions[2], running.version)
            if ready:
                return complete
