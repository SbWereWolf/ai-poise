"""Batch planning/publication through Sprint and Task owners, one local UoW."""
from copy import deepcopy
import hashlib
import json
from dataclasses import replace
from ..modules.sprints.domain import SprintPolicy, SprintPlan, Sprint, task_identity
from ..modules.tasks.definition import path_identifier, validate_creation
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import PoiseError, DomainError, VersionConflict
from ..modules.result_integration.domain import IntegrationHistoricalBackupScope
from .tasks import (
    creation_batch_reservations, prepare_existing_creation_context,
    rewrite_task_plan, validate_creation_intent,
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _creation_definition(body):
    """Return the Task schema owned below the external Requirements gate."""
    result = deepcopy(body)
    result.pop("requirements_snapshot", None)
    result.pop("requirements_agreement", None)
    return result


def require_sprint_execution_in(uow, task_id):
    """Sprint-owned admission shared by Task and handoff application commands."""
    snapshot = uow.tasks.membership_snapshot(task_id)
    sid = snapshot['sprint_id']
    if sid is None:
        return
    record = uow.sprints.get(sid)
    if record is None:
        raise DomainError('Task is not a published Sprint member')
    Sprint.restore(record['aggregate']).require_execution(task_id)


def require_sprint_execution(inputs, task_id):
    sid = inputs.restart_context(task_id)['sprint_id']
    if sid is None:
        return
    record = inputs.sprint(sid)
    if record is None:
        raise DomainError('Task is not a published Sprint member')
    Sprint.restore(record['aggregate']).require_execution(task_id)


class SprintCommands:
    def __init__(self,unit_of_work,project,actor,policy,task_id_policy,workflows,
                 automatic_checks,decomposition_policy,execution_hash,
                 prepare_creation,creation_base,prepare_newborn_ready,promote_newborn_ready_in,
                 publication_target,backup_scope):
        self.uow=unit_of_work;self.project=project;self.actor=actor
        self.policy=SprintPolicy.parse(policy);self.workflows=deepcopy(workflows)
        self.task_id_policy=task_id_policy
        self.automatic_checks=deepcopy(automatic_checks);self.execution_hash=execution_hash
        self.decomposition_policy=deepcopy(decomposition_policy)
        self.prepare_creation=prepare_creation;self.creation_base=creation_base
        self.prepare_newborn_ready=prepare_newborn_ready
        self.promote_newborn_ready_in=promote_newborn_ready_in
        self.publication_target=publication_target
        self.backup_scope=backup_scope

    def _publication_scope_for(self, source, facts):
        if any(fact['status'] == 'completed' and isinstance(fact['pending'], dict)
               and fact['pending'].get('kind') == 'result_integration'
               for fact in facts.values()):
            versions = {task_id: fact['version'] for task_id, fact in facts.items()
                        if fact['status'] == 'completed'}
            scope = self.backup_scope()
            historical = IntegrationHistoricalBackupScope(
                scope.runtime_root, versions, source.integration_records(list(versions)))
            return {'publication_target':self.publication_target(),
                    'backup_scope':historical}
        return {'publication_target':None,'backup_scope':None}

    def _errors(self,record,facts,inputs,newborns):
        s=Sprint.restore(record['aggregate']);errors=s.plan.content_errors(s.policy)+s.plan.graph_errors(s.policy)
        for t in s.plan.data['tasks']:
            try:
                if isinstance(t,str):
                    fact=facts.get(t)
                    if fact is None:raise DomainError('Newborn Sprint member does not exist')
                    if fact['status']!='newborn':
                        continue
                    newborn = newborns[t] if t in newborns else inputs.newborn(t)
                    body={'id':t,'sprint_id':s.plan.data['id'],**deepcopy(newborn.draft)}
                    workflow=newborn.workflow
                    if workflow is None:raise DomainError('Unknown goal_type')
                    definition = _creation_definition(body)
                    context = prepare_existing_creation_context(newborn, inputs, definition, request_id=None)
                    validation = {} if context is None else {
                        'current_registry': context['current_registry'],
                        'existing_contract': context['existing_contract'],
                    }
                    validate_creation(
                        definition, workflow, record['automatic_checks'],
                        record['task_decomposition'], **validation,
                    )
                    if not newborn.ready:
                        raise DomainError('Newborn Sprint member is not type-ready')
                    continue
                body=t['task'] if isinstance(t,dict) and set(t)=={'request_id','task'} else t
                kind=body.get('goal_type')
                if not isinstance(kind,str) or kind not in record['workflows']:raise DomainError('Unknown goal_type')
                validate_creation_intent(
                    ({**t, 'task': _creation_definition(body)} if 'task' in t
                     else _creation_definition(body)), record['workflows'][kind], record['automatic_checks'],
                    record['task_decomposition'],
                )
            except PoiseError as exc:errors.append(f"Task creation intent: {exc}")
        return errors

    @staticmethod
    def _available_membership(snapshot, expected_sprint):
        from .sprint_preparation import require_available_membership
        return require_available_membership(snapshot, expected_sprint)

    @staticmethod
    def _validate_dependency_membership(plan):
        ids={task_identity(item) for item in plan.data['tasks']}
        if any(
            edge['predecessor'] not in ids or edge['successor'] not in ids
            for edge in plan.data['dependencies']
        ):
            raise DomainError('Every dependency endpoint must belong to the same Sprint')

    def _describe_facts(self, record, facts, inputs, newborns):
        s=Sprint.restore(record['aggregate']);sid=s.plan.data['id']
        status=s.work_overview(facts)
        return {**status,'sprint':sid,'goal':s.plan.data['goal'],'revision':s.revision,
            'dependencies':deepcopy(s.plan.data['dependencies']),
            'tasks':[{'id':i,**{k:value for k,value in v.items()
                                if not k.startswith('_')}} for i,v in facts.items()],
            'counts':{k:sum(f['status']==k for f in facts.values()) for k in ('newborn','available','active','verified','accepted','completed','cancelled')},
            'errors':self._errors(record,facts,inputs,newborns) if s.state=='draft' else [],
            'next_work':'Исправить draft и опубликовать одним пакетом' if s.state=='draft' else
                        'Выбрать одну доступную задачу; продолжать поручение через advance с действующими ограничениями',
            'active_task':None}

    @staticmethod
    def _action(packet):
        shapes={'draft':{'action','sprint_id','request_id','expected_revision','template','changes'},
                'publish':{'action','sprint_id','request_id','expected_revision'},
                'unpublish':{'action','sprint_id','request_id','expected_revision','reason','authorization'},
                'materialize_tasks':{'action','sprint_id','request_id','expected_revision'},
                'extract_tasks':{'action','sprint_id','request_id','expected_revision','task_ids'},
                'dependencies':{'action','sprint_id','request_id','expected_revision','items','reason'},
                'cancel_tasks':{'action','sprint_id','request_id','tasks','mode','reason'},
                'cancel':{'action','sprint_id','request_id','reason'},
                'waive_dependencies':{'action','sprint_id','request_id','decisions'}}
        if not isinstance(packet,dict) or not isinstance(packet.get('action'),str) or packet['action'] not in shapes:
            raise DomainError('Unknown sprint action')
        exact_keys(packet,shapes[packet['action']],'sprint action');path_identifier(packet['request_id'])
        return packet['action']

    def cancellation_snapshot(self, packet):
        action = self._action(packet)
        if action not in ('cancel_tasks','cancel'):
            raise DomainError('Cancellation snapshot requires cancel action')
        identity = fingerprint(packet)
        snapshot, _ = self._capture_packet(packet)
        sid = path_identifier(snapshot.sprint_id)
        record = snapshot.record()
        if record is None or record['project'] != self.project:
            raise DomainError('Unknown Sprint in this project')
        old = snapshot.receipt(packet['request_id'], identity)
        if old is not None:
            return {'receipt':old, 'sprint':sid}
        sprint = Sprint.restore(record['aggregate'])
        requested = ([task_identity(t) for t in sprint.plan.data['tasks']] if sprint.state == 'published' else []) if action == 'cancel' else list(sprint.cancellation_scope(packet['tasks'], packet['mode']))
        facts = snapshot.facts()
        ids = [tid for tid in requested if facts[tid]['status'] not in ('completed','cancelled')]
        snapshots = {}
        for tid in ids:
            if facts[tid]['pending'] is not None:
                raise DomainError('External operation outcome must be resolved first')
            task = snapshot.inputs.task(tid)
            snapshots[tid] = {'fact':facts[tid], 'task_version':task.state.version}
        return {'receipt':None, 'sprint':sid, 'tasks':snapshots}

    def publication_preflight(self, packet, identity):
        snapshot = self.uow().capture_sprint(packet['sprint_id'], self.actor)
        sid = path_identifier(snapshot.sprint_id)
        record = snapshot.record()
        if record is None:
            raise DomainError('Sprint does not exist')
        if record['project'] != self.project:
            raise DomainError('Sprint belongs to another project')
        if snapshot.receipt(packet['request_id'], identity) is not None:
            return None
        if type(packet['expected_revision']) is not int or packet['expected_revision'] != record['aggregate']['revision']:
            raise VersionConflict('Publish revision changed')
        sprint = Sprint.restore(record['aggregate'])
        errors = sprint.plan.content_errors(sprint.policy) + sprint.plan.graph_errors(sprint.policy)
        if errors:
            raise DomainError('; '.join(errors))
        facts = snapshot.facts()
        if sprint.reopened:
            sprint.require_released_members(facts, **self._publication_scope_for(snapshot, facts))
        errors = sprint.republication_errors()
        if errors:
            raise DomainError('; '.join(errors))
        errors = self._errors(record, facts, snapshot.inputs, {})
        if errors:
            raise DomainError('; '.join(errors))
        base = self.creation_base()
        prepared = []
        for intent in sprint.plan.data['tasks']:
            if isinstance(intent, str):
                member = snapshot.inputs.membership_snapshot(intent)
                if member['status'] != 'newborn':
                    if not sprint.reopened:
                        self._available_membership(member, sid)
                    prepared.append({'creation':None, 'newborn':None, 'existing':member})
                    continue
                newborn = snapshot.inputs.newborn(intent)
                if newborn.claimed_by not in (None, self.actor):
                    raise DomainError('Newborn Sprint member is owned by another session')
                candidate = self.prepare_newborn_ready(intent, self.actor, record['automatic_checks'],
                    record['task_decomposition'], record['execution_hash'], packet['request_id']+':'+intent, lambda:base)
                if candidate['snapshot'] != newborn:
                    raise VersionConflict('Newborn Sprint member changed during preparation')
                if not newborn.ready:
                    raise DomainError('Newborn Sprint member is not type-ready')
                prepared.append({'creation':candidate['creation'], 'newborn':newborn, 'candidate':candidate})
            else:
                body = intent['task'] if isinstance(intent, dict) and set(intent) == {'request_id','task'} else intent
                prepared.append({'creation':self.prepare_creation(intent, record['workflows'][body['goal_type']],
                    record['automatic_checks'], base, record['task_decomposition']), 'newborn':None})
        return {'record':fingerprint(record), 'source_record':record, 'snapshot':snapshot,
                'prepared':prepared, 'facts':facts}

    def _publish(self, packet, identity):
        from .task_preparation import compile_creation_batch
        from ..modules.tasks.ports import TaskReadSet, NewbornPromotionBatch
        from ..modules.tasks.allocation import creation_parts
        facade = self.uow()
        snapshot = facade.capture_sprint(packet['sprint_id'], self.actor)
        sid = path_identifier(snapshot.sprint_id)
        with self.uow() as u:
            replay = u.sprints.receipt_raw(sid, packet['request_id'], identity)
            if replay is not None:
                u.sprints.bind(self.actor, sid)
        if replay is not None:
            return facade.restore_sprint_receipt(replay)
        publication = self.publication_preflight(packet, identity)
        if publication is None:
            with self.uow() as u:
                replay = u.sprints.receipt_raw(sid, packet['request_id'], identity)
                if replay is not None:
                    u.sprints.bind(self.actor, sid)
            if replay is None:
                raise VersionConflict('Sprint publication receipt changed; repeat the request')
            return facade.restore_sprint_receipt(replay)
        snapshot = publication['snapshot']
        if snapshot.sprint_id != sid:
            raise VersionConflict('Selected Sprint changed during preflight; repeat the request')
        record = publication['source_record']
        sprint = Sprint.restore(record['aggregate']).publish()
        embedded = tuple(item['creation'] for item in publication['prepared'] if item['newborn'] is None and item.get('existing') is None)
        promotions = tuple(item['candidate'] for item in publication['prepared'] if item['newborn'] is not None)
        requests = tuple(request_id for prepared in embedded
                         if (request_id := creation_parts(prepared.source_intent)[0]) is not None)
        existing_ids = tuple(item for item in sprint.plan.data['tasks'] if isinstance(item, str))
        reservations = creation_batch_reservations([p.source_intent for p in embedded], (sid, *existing_ids))
        while True:
            namespace = facade.capture(TaskReadSet((), allocation=True, creation_requests=requests))
            writes = compile_creation_batch(facade, embedded, namespace,
                {'config_hash':record['execution_hash']}, self.task_id_policy, reservations)
            allocations = [write.allocation.receipt() for write in writes if write.allocation.request_id is not None]
            allocations = [item for item in allocations if item is not None]
            for item in publication['prepared']:
                newborn = item['newborn']
                if newborn is not None and newborn.creation_request is not None:
                    allocations.append({'request_id':newborn.creation_request['request_id'],
                                        'task_id':newborn.task_id, 'replayed':False})
            resolved = sprint
            if writes:
                contracts = {write.allocation.request_id if write.allocation.request_id is not None
                             else write.allocation.task_id:write.contract for write in writes}
                resolved = replace(sprint, plan=SprintPlan(rewrite_task_plan(sprint.plan.data, contracts)))
            updated = {**record, 'actor':self.actor, 'aggregate':resolved.to_dict()}
            sprint_write = facade.prepare_sprint(updated)
            candidates = [facade.promotion_member_fact(snapshot, item) for item in promotions]
            candidates.extend(facade.creation_member_fact(write, snapshot) for write in writes)
            result = self._describe_facts(updated, snapshot.facts(updated, candidates), snapshot.inputs, {})
            if allocations:
                result = {**result, 'allocations':allocations}
            receipt = facade.prepare_sprint_receipt(result)
            with self.uow() as u:
                replay = u.sprints.receipt_raw(sid, packet['request_id'], identity)
                ready = replay is None and all(u.tasks.require_creation_allocation(write.allocation, write.receipt) for write in writes)
                if ready:
                    u.require_sprint(snapshot)
                    self.promote_newborn_ready_in(u, NewbornPromotionBatch(promotions), self.actor)
                    for write in writes:
                        write.save(u)
                    u.sprints.save_prepared(sprint_write, record['aggregate']['revision'])
                    if resolved.reopened:
                        u.sprints.replace_prepared_dependencies(sprint_write)
                    u.sprints.publish_prepared_members(sprint_write)
                    u.sprints.bind(self.actor, sid)
                    u.sprints.remember_prepared(sid, packet['request_id'], identity, receipt)
                elif replay is not None:
                    u.sprints.bind(self.actor, sid)
            if replay is not None:
                return facade.restore_sprint_receipt(replay)
            if ready:
                return result

    def _capture_packet(self, packet):
        task_ids, requests = set(), set()
        def collect(item):
            if isinstance(item, str):
                task_ids.add(item)
            elif isinstance(item, dict):
                if set(item) == {'request_id','task'} and isinstance(item['request_id'], str):
                    requests.add(item['request_id'])
                elif isinstance(item.get('id'), str):
                    task_ids.add(item['id'])
        for key in ('task_ids','tasks'):
            if isinstance(packet.get(key), list):
                for item in packet[key]:
                    collect(item)
        if isinstance(packet.get('changes'), list):
            for change in packet['changes']:
                if not isinstance(change, dict):
                    continue
                for key in ('tasks','ids'):
                    if isinstance(change.get(key), list):
                        for item in change[key]:
                            collect(item)
        facade = self.uow()
        snapshot = facade.capture_sprint(packet['sprint_id'], self.actor, tuple(sorted(task_ids)), tuple(sorted(requests)))
        initial_ids, initial_requests = set(task_ids), set(requests)
        record = snapshot.record()
        if record is not None:
            for item in record['aggregate']['plan']['tasks']:
                collect(item)
        if initial_ids != task_ids or initial_requests != requests:
            selected = snapshot.sprint_id
            snapshot = facade.capture_sprint(packet['sprint_id'], self.actor, tuple(sorted(task_ids)), tuple(sorted(requests)))
            if selected != snapshot.sprint_id:
                raise VersionConflict('Selected Sprint changed; repeat the request')
        return snapshot, tuple(sorted(requests))

    def _prepare_mutation(self, snapshot, namespace, packet, preflight):
        from .sprint_preparation import SprintTaskChanges, prepare_draft_changes, require_available_membership
        from .task_cleanup import prepare_cleanup_execution
        facade = self.uow()
        action = packet['action']
        sid = path_identifier(snapshot.sprint_id)
        record = snapshot.record()
        if record is not None and record['project'] != self.project:
            raise DomainError('Sprint belongs to another project')
        tasks = SprintTaskChanges(facade, snapshot, self.actor)
        task_writes, execution_writes, extra_facts = [], [], []
        materialized = None
        if action == 'draft':
            expected = packet['expected_revision']
            previous_members = set()
            if record is None:
                if expected is not None:
                    raise VersionConflict('New sprint requires explicit null revision')
                changes, adoptions = prepare_draft_changes(packet['changes'], sid, self.workflows,
                    self.execution_hash, self.automatic_checks, self.decomposition_policy,
                    self.task_id_policy, tasks, namespace)
                s = Sprint.draft(SprintPlan.create(sid, packet['template'], self.policy), self.policy)
                plan = s.plan.apply(changes, s.policy)
                self._validate_dependency_membership(plan)
                s = Sprint(plan, s.policy, s.revision, s.state, s.waivers, s.decisions)
                record = {'project':self.project, 'actor':self.actor, 'aggregate':s.to_dict(),
                    'workflows':deepcopy(self.workflows), 'automatic_checks':deepcopy(self.automatic_checks),
                    'task_decomposition':deepcopy(self.decomposition_policy), 'execution_hash':self.execution_hash}
            else:
                if type(expected) is not int or expected != record['aggregate']['revision']:
                    raise VersionConflict('Draft revision changed')
                if packet['template'] is not None:
                    raise DomainError('Edit uses saved template/policy; explicit null required')
                if record['aggregate']['state'] != 'draft':
                    raise DomainError('Published sprint draft cannot be silently rewritten')
                s = Sprint.restore(record['aggregate'])
                previous_members = {item for item in s.plan.data['tasks'] if isinstance(item, str)}
                changes, adoptions = prepare_draft_changes(packet['changes'], sid, record['workflows'],
                    record['execution_hash'], self.automatic_checks, self.decomposition_policy,
                    self.task_id_policy, tasks, namespace)
                plan = s.plan.apply(changes, s.policy)
                self._validate_dependency_membership(plan)
                old_edges = {(e['predecessor'],e['successor'],e['kind']) for e in s.plan.data['dependencies']}
                new_edges = {(e['predecessor'],e['successor'],e['kind']) for e in plan.data['dependencies']}
                if s.reopened:
                    affected = {b for a,b,k in old_edges.symmetric_difference(new_edges)}
                    if any(tasks.membership(tid)['status'] not in ('newborn','available') for tid in affected):
                        raise DomainError('Changed prerequisites of started members require authorized restart')
                unchanged = old_edges & new_edges
                s = replace(s.revise(plan), waivers=tuple(w for w in s.waivers if any(
                    (w['predecessor'],w['successor']) == (a,b) for a,b,k in unchanged)))
                record = {**record, 'actor':self.actor, 'aggregate':s.to_dict()}
            for task_id in adoptions:
                tasks.move(task_id, None, sid)
                tasks.additions.append(task_id)
            current_members = {item for item in s.plan.data['tasks'] if isinstance(item, str)}
            tasks.detach(sid, sorted(previous_members - current_members))
        else:
            if record is None:
                raise DomainError('Sprint does not exist')
            s = Sprint.restore(record['aggregate'])
            expected = s.revision
            facts = snapshot.facts()
            if action == 'unpublish':
                if type(packet['expected_revision']) is not int or packet['expected_revision'] != expected:
                    raise VersionConflict('Unpublish revision changed')
                s = s.unpublish(facts, packet['reason'], packet['authorization'],
                                **self._publication_scope_for(snapshot, facts))
            elif action == 'materialize_tasks':
                if s.state != 'draft':
                    raise DomainError('Only a legacy Sprint draft can materialize Tasks')
                if type(packet['expected_revision']) is not int or packet['expected_revision'] != expected:
                    raise VersionConflict('Materialization revision changed')
                legacy = list(s.plan.data['tasks'])
                if not legacy or any(isinstance(item, str) for item in legacy):
                    raise DomainError('Sprint draft has no legacy embedded Task definitions')
                converted, _ = prepare_draft_changes([{'kind':'upsert_tasks', 'tasks':legacy}], sid,
                    record['workflows'], record['execution_hash'], self.automatic_checks,
                    self.decomposition_policy, self.task_id_policy, tasks, namespace)
                materialized = list(converted[0]['tasks'])
                plan = deepcopy(s.plan.data)
                plan['tasks'] = sorted(materialized)
                s = replace(s, plan=SprintPlan(plan), revision=expected + 1)
            elif action == 'extract_tasks':
                if type(packet['expected_revision']) is not int or packet['expected_revision'] != expected:
                    raise VersionConflict('Extraction revision changed')
                s = s.extract_tasks(packet['task_ids'])
                for task_id in packet['task_ids']:
                    require_available_membership(tasks.membership(task_id), sid)
                for task_id in packet['task_ids']:
                    tasks.move(task_id, sid, None)
                    tasks.removals.append(task_id)
            elif action == 'dependencies':
                if type(packet['expected_revision']) is not int or packet['expected_revision'] != expected:
                    raise VersionConflict('Dependency revision changed')
                s = s.update_dependencies(packet['items'], {i:f['status'] for i,f in facts.items()}, packet['reason'])
            elif action == 'waive_dependencies':
                if not isinstance(packet['decisions'], list) or not packet['decisions']:
                    raise DomainError('A nonempty explicit decision batch is required')
                s = s.waive(packet['decisions'])
            elif action in ('cancel_tasks','cancel'):
                whole = action == 'cancel'
                if s.state not in ('draft','published'):
                    raise DomainError('Sprint already cancelled')
                if not whole and s.state != 'published':
                    raise DomainError('Only published members may be cancelled')
                draft_members = [t for t in s.plan.data['tasks'] if isinstance(t, str)] if whole and s.state == 'draft' else []
                requested = ([task_identity(t) for t in s.plan.data['tasks']] if s.state == 'published' else []) if whole else list(s.cancellation_scope(packet['tasks'], packet['mode']))
                ids = [tid for tid in requested if facts[tid]['status'] not in ('completed','cancelled')]
                for tid in ids:
                    if facts[tid]['pending'] is not None:
                        raise DomainError('External operation outcome must be resolved first')
                    if preflight is None or tid not in preflight['tasks']:
                        raise DomainError('Cancellation requires external resource preflight')
                    prior = preflight['tasks'][tid]
                    task = snapshot.inputs.task(tid)
                    if task.state.version != prior['task_version'] or facts[tid] != prior['fact']:
                        raise VersionConflict('Task changed after cancellation preflight')
                    change = task.cancel_from_sprint(packet['reason'])
                    write = facade.prepare_change(snapshot.inputs, change)
                    task_writes.append(write)
                    current = snapshot.inputs.execution(tid)
                    data = prepare_cleanup_execution(None if current is None else current[0], prior['cleanup_pending'])
                    execution = facade.prepare_execution(snapshot.inputs, tid, data)
                    execution_writes.append((tid, None if current is None else current[1], execution))
                    extra_facts.append(facade.member_fact_with_execution(facade.prepared_member_fact(snapshot, write), execution))
                tasks.detach(sid, draft_members)
                s = s.cancel(ids, packet['reason']) if whole else s.record_task_cancellation(ids, packet['reason'])
            record = {**record, 'actor':self.actor, 'aggregate':s.to_dict()}
        facts = snapshot.facts(record, [*tasks.facts.values(), *extra_facts])
        result = self._describe_facts(record, facts, snapshot.inputs, tasks.newborns)
        if materialized is not None:
            result = {**result, 'materialized':sorted(materialized)}
        if action == 'extract_tasks':
            result = {**result, 'extracted':sorted(packet['task_ids'])}
        if action in ('cancel_tasks','cancel'):
            result = {**result, 'cleanup':{tid:item['cleanup_result'] for tid,item in preflight['tasks'].items()}}
        return tasks, task_writes, execution_writes, facade.prepare_sprint(record), expected, result

    def apply(self, packet, preflight=None):
        from ..modules.tasks.ports import TaskReadSet
        action = self._action(packet)
        identity = fingerprint(packet)
        if action == 'publish':
            return self._publish(packet, identity)
        facade = self.uow()
        snapshot, requests = self._capture_packet(packet)
        sid = path_identifier(snapshot.sprint_id)
        with self.uow() as u:
            replay = u.sprints.receipt_raw(sid, packet['request_id'], identity)
            if replay is not None:
                u.sprints.bind(self.actor, sid)
        if replay is not None:
            return facade.restore_sprint_receipt(replay)
        namespace = snapshot.inputs
        while True:
            tasks, task_writes, execution_writes, sprint_write, expected, result = self._prepare_mutation(snapshot, namespace, packet, preflight)
            receipt = facade.prepare_sprint_receipt(result)
            with self.uow() as u:
                replay = u.sprints.receipt_raw(sid, packet['request_id'], identity)
                ready = replay is None and all(u.tasks.require_creation_allocation(allocation, prior)
                    for allocation, prior in tasks.allocations)
                if ready:
                    u.require_sprint(snapshot)
                    tasks.save(u)
                    for write in task_writes:
                        u.tasks.save_prepared(write)
                    for task_id, version, execution in execution_writes:
                        if version is None:
                            u.execution.create_prepared(task_id, execution)
                        else:
                            u.execution.save_prepared(task_id, execution, version)
                    u.sprints.save_prepared(sprint_write, expected)
                    if action == 'dependencies':
                        u.sprints.replace_prepared_dependencies(sprint_write)
                    for task_id in tasks.removals:
                        u.sprints.remove_draft_member(sid, task_id)
                    for task_id in tasks.additions:
                        u.sprints.add_draft_member(sid, task_id)
                    u.sprints.bind(self.actor, sid)
                    u.sprints.remember_prepared(sid, packet['request_id'], identity, receipt)
                elif replay is not None:
                    u.sprints.bind(self.actor, sid)
            if replay is not None:
                return facade.restore_sprint_receipt(replay)
            if ready:
                return result
            namespace = facade.capture(TaskReadSet((), allocation=True, creation_requests=requests))

    def known(self,sprint_id):
        with self.uow() as u:return u.sprints.get(sprint_id) is not None

    def overview_ids(self):
        with self.uow() as u:return u.sprints.published_ids(self.project)

    def read(self, sprint_id, view):
        facade = self.uow()
        snapshot = (facade.capture_sprint(sprint_id, self.actor, (), ()) if view == 'current'
                    else facade.capture_sprint_view(sprint_id, self.actor, view))
        sid = snapshot.sprint_id
        if sid is None:
            return None
        record = snapshot.record()
        if record is None or record['project'] != self.project:
            raise DomainError('Unknown sprint in this project')
        if view == 'history':
            return {'sprint':sid, 'layers':snapshot.layers()}
        if view == 'plan':
            return deepcopy(record)
        if view != 'current':
            raise DomainError('Unknown sprint view')
        return self._describe_facts(record, snapshot.facts(), snapshot.inputs, {})

    def select(self, sprint_id):
        snapshot = self.uow().capture_sprint_view(sprint_id, self.actor, 'plan')
        record = snapshot.record()
        if record is None or record['project'] != self.project:
            raise DomainError('Unknown sprint')
        with self.uow() as u:
            u.require_inputs(snapshot.inputs)
            u.sprints.bind(self.actor, sprint_id)
        return self.read(sprint_id, 'current')
