"""Batch planning/publication through Sprint and Task owners, one local UoW."""
from copy import deepcopy
import hashlib
import json
from dataclasses import replace
from ..modules.sprints.domain import SprintPolicy, SprintPlan, Sprint, task_identity
from ..modules.tasks.definition import build_task, path_identifier, validate_creation
from ..modules.tasks.newborn import NewbornTask
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import PoiseError, DomainError, VersionConflict
from .tasks import (create_planned_in_uow, creation_batch_reservations, creation_intent_alias,
                    rewrite_task_plan, validate_creation_intent)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _creation_definition(body):
    """Return the Task schema owned below the external Requirements gate."""
    result = deepcopy(body)
    result.pop("requirements_snapshot", None)
    result.pop("requirements_agreement", None)
    return result


class SprintCommands:
    def __init__(self,unit_of_work,project,actor,policy,task_id_policy,processes,
                 automatic_checks,decomposition_policy,execution_hash,
                 prepare_creation,creation_base):
        self.uow=unit_of_work;self.project=project;self.actor=actor
        self.policy=SprintPolicy.parse(policy);self.processes=deepcopy(processes)
        self.task_id_policy=task_id_policy
        self.automatic_checks=deepcopy(automatic_checks);self.execution_hash=execution_hash
        self.decomposition_policy=deepcopy(decomposition_policy)
        self.prepare_creation=prepare_creation;self.creation_base=creation_base

    def _errors(self,record,facts=None):
        s=Sprint.restore(record['aggregate']);errors=s.plan.content_errors(s.policy)+s.plan.graph_errors(s.policy)
        if facts is None:
            with self.uow() as uow:
                facts=uow.sprints.facts(s.plan.data['id'])
        for t in s.plan.data['tasks']:
            try:
                if isinstance(t,str):
                    fact=facts.get(t)
                    if fact is None:raise DomainError('Newborn Sprint member does not exist')
                    if fact['status']!='newborn':
                        continue
                    body={'id':t,'sprint_id':s.plan.data['id'],**deepcopy(fact['_newborn_draft'])}
                    process=fact['_newborn_process']
                    if process is None:raise DomainError('Unknown goal_type')
                    validate_creation(
                        _creation_definition(body), process, record['automatic_checks'],
                        record['task_decomposition'],
                    )
                    if not fact.get('ready',False):
                        raise DomainError('Newborn Sprint member is not type-ready')
                    continue
                body=t['task'] if isinstance(t,dict) and set(t)=={'request_id','task'} else t
                kind=body.get('goal_type')
                if not isinstance(kind,str) or kind not in record['processes']:raise DomainError('Unknown goal_type')
                validate_creation_intent(
                    ({**t, 'task': _creation_definition(body)} if 'task' in t
                     else _creation_definition(body)), record['processes'][kind], record['automatic_checks'],
                    record['task_decomposition'],
                )
            except PoiseError as exc:errors.append(f"Task creation intent: {exc}")
        return errors

    @staticmethod
    def _save_cleanup(uow, task_id, pending):
        if not uow.execution.exists(task_id):
            uow.execution.create(task_id,{"worktree":None,"branch":None,"base":None,"attempts":0,
                "publication":None,"pending":pending,"entry_tree":None,"last_report":None})
            return
        execution,version=uow.execution.load(task_id)
        if execution['pending'] is not None:raise DomainError('External operation outcome must be resolved first')
        execution['pending']=pending
        uow.execution.save(task_id,execution,version)

    @staticmethod
    def _available_membership(snapshot, expected_sprint):
        if snapshot['sprint_id'] != expected_sprint:
            raise DomainError('Task belongs to another Sprint')
        if snapshot['claimed_by'] is not None:
            raise DomainError('Membership conversion requires an unclaimed available Task')
        execution=snapshot['execution']
        if execution is not None and (execution['pending'] is not None
                                      or execution['publication'] is not None):
            raise DomainError('Task has a pending external operation')
        if execution is not None and execution['last_report'] is not None:
            raise DomainError('Task has an existing result')
        started=(snapshot['status']!='available' or execution is not None and (
            execution['worktree'] is not None or execution['branch'] is not None
            or execution['base'] is not None or execution['entry_tree'] is not None
            or execution['attempts'] != 0
        ))
        if started:
            raise DomainError('Membership conversion requires a Task that was never started and has no worktree')

    def _adoption_changes(self,uow,sid,changes):
        prepared=deepcopy(changes);adoption=None
        for change in prepared:
            if not isinstance(change,dict):continue
            if change.get('kind')!='adopt_tasks':continue
            if adoption is not None:raise DomainError('Conflicting duplicate sprint change kind')
            exact_keys(change,{'kind','ids'},'adopt_tasks')
            ids=change['ids']
            if (not isinstance(ids,list) or not ids
                    or any(not isinstance(item,str) for item in ids)
                    or len(ids)!=len(set(ids))):
                raise DomainError('adopt_tasks requires unique Task IDs')
            adoption=list(ids)
        if adoption is None:return prepared,{}
        snapshots={}
        for task_id in adoption:
            path_identifier(task_id)
            snapshot=uow.tasks.membership_snapshot(task_id)
            self._available_membership(snapshot,None)
            snapshots[task_id]=snapshot
        prepared=[change for change in prepared if change.get('kind')!='adopt_tasks']
        upsert=next((change for change in prepared if change.get('kind')=='upsert_tasks'),None)
        if upsert is None:
            prepared.append({'kind':'upsert_tasks','tasks':list(adoption)})
        else:
            upsert['tasks']=[*upsert['tasks'],*adoption]
        return prepared,snapshots

    def _detach_draft_members(self,uow,sid,task_ids):
        adopted={}
        newborn=[]
        for task_id in task_ids:
            if uow.tasks.is_newborn(task_id):
                newborn.append(task_id)
                continue
            snapshot=uow.tasks.membership_snapshot(task_id)
            self._available_membership(snapshot,sid)
            adopted[task_id]=snapshot
        for task_id in newborn:
            uow.tasks.detach_newborn(task_id,sid)
        for task_id,snapshot in adopted.items():
            uow.tasks.change_sprint_membership(
                task_id,sid,None,snapshot['version']
            )
        for task_id in task_ids:
            uow.sprints.remove_draft_member(sid,task_id)

    def _materialize_draft_changes(self,uow,sid,changes,processes,execution_hash):
        """Replace embedded definitions with editable real newborn Task members."""
        from ..modules.tasks.allocation import TaskIdPolicy, materialize_contract
        prepared,adoptions=self._adoption_changes(uow,sid,changes)
        members=[];aliases={}
        for change in prepared:
            if change.get('kind')!='upsert_tasks':continue
            normalized=[]
            for intent in change['tasks']:
                if isinstance(intent,str):
                    if intent in adoptions:
                        aliases[intent]=intent
                        normalized.append(intent);continue
                    newborn=uow.tasks.load_newborn(intent)
                    if newborn.sprint_id!=sid:
                        raise DomainError('Newborn Task belongs to another Sprint')
                    aliases[intent]=intent
                    normalized.append(intent);continue
                creation_request=None
                if isinstance(intent,dict) and set(intent)=={'request_id','task'}:
                    policy=TaskIdPolicy.parse(self.task_id_policy)
                    allocation=uow.tasks.allocate(intent,policy,(sid,*normalized))
                    contract,creation_request=materialize_contract(intent,allocation.task_id)
                    alias=allocation.request_id
                else:
                    contract=deepcopy(intent)
                    alias=contract.get('id') if isinstance(contract,dict) else None
                if not isinstance(contract,dict) or not isinstance(contract.get('id'),str):
                    raise DomainError('Sprint draft Task requires an explicit identity')
                task_id=path_identifier(contract['id'])
                draft={k:deepcopy(v) for k,v in contract.items() if k not in ('id','sprint_id')}
                existing=uow.tasks.exists(task_id)
                if existing:
                    if not uow.tasks.is_newborn(task_id):
                        aliases[alias]=task_id
                        normalized.append(task_id)
                        continue
                    newborn=uow.tasks.load_newborn(task_id)
                    if newborn.sprint_id!=sid:
                        raise DomainError('Newborn Task belongs to another Sprint')
                    if creation_request is not None and newborn.creation_request!=creation_request:
                        raise DomainError('Creation request belongs to another newborn Task')
                    updates={k:v for k,v in draft.items() if newborn.draft.get(k)!=v}
                    if updates:
                        if newborn.claimed_by not in (None,self.actor):
                            raise DomainError('Newborn Task is owned by another session')
                        changed=newborn.materialize_draft(updates,processes,self.actor)
                        changed=replace(changed,claimed_by=newborn.claimed_by)
                        uow.tasks.save_newborn(
                            changed,newborn.version,execution_hash,'newborn_edited'
                        )
                        newborn=changed
                else:
                    newborn=replace(
                        NewbornTask.create(task_id,sid,None),
                        creation_request=deepcopy(creation_request),
                    )
                    uow.tasks.create_newborn(newborn,execution_hash)
                    members.append(task_id)
                    if draft:
                        changed=newborn.materialize_draft(draft,processes,self.actor)
                        changed=replace(changed,claimed_by=None)
                        uow.tasks.save_newborn(
                            changed,newborn.version,execution_hash,'newborn_edited'
                        )
                        newborn=changed
                try:
                    process=newborn.process
                    if process is None:raise DomainError('Unknown goal_type')
                    body={'id':task_id,'sprint_id':sid,**deepcopy(newborn.draft)}
                    validate_creation(
                        _creation_definition(body), process, self.automatic_checks,
                        self.decomposition_policy,
                    )
                except PoiseError:
                    pass
                else:
                    if not newborn.ready:
                        ready=newborn.mark_ready()
                        uow.tasks.save_newborn(
                            ready,newborn.version,execution_hash,'newborn_ready'
                        )
                        newborn=ready
                normalized.append(task_id)
                aliases[alias]=task_id
            change['tasks']=normalized
        for change in prepared:
            if change.get('kind')!='dependencies':continue
            change['items']=[{
                **edge,
                'predecessor':aliases.get(edge['predecessor'],edge['predecessor']),
                'successor':aliases.get(edge['successor'],edge['successor']),
            } for edge in change['items']]
        return prepared,members,adoptions

    @staticmethod
    def _validate_dependency_membership(plan):
        ids={task_identity(item) for item in plan.data['tasks']}
        if any(
            edge['predecessor'] not in ids or edge['successor'] not in ids
            for edge in plan.data['dependencies']
        ):
            raise DomainError('Every dependency endpoint must belong to the same Sprint')

    def _describe(self,uow,record):
        s=Sprint.restore(record['aggregate']);sid=s.plan.data['id'];facts=uow.sprints.facts(sid)
        resumable=tuple(i for i,f in facts.items() if f['claimed_by'] is None and f['handoff_available']
                        and f['status'] in ('active','verified','accepted'))
        status=s.overview({i:f['status'] for i,f in facts.items()},resumable)
        return {**status,'resumable':list(resumable),'sprint':sid,'goal':s.plan.data['goal'],'revision':s.revision,
            'dependencies':deepcopy(s.plan.data['dependencies']),
            'tasks':[{'id':i,**{k:value for k,value in v.items()
                                if not k.startswith('_')}} for i,v in facts.items()],
            'counts':{k:sum(f['status']==k for f in facts.values()) for k in ('available','active','verified','accepted','completed','cancelled')},
            'errors':self._errors(record,facts) if s.state=='draft' else [],
            'next_work':'Исправить draft и опубликовать одним пакетом' if s.state=='draft' else
                        'Выбрать одну доступную задачу; выполнить один этап и доложить',
            'active_task':None}

    @staticmethod
    def _action(packet):
        shapes={'draft':{'action','sprint_id','request_id','expected_revision','template','changes'},
                'publish':{'action','sprint_id','request_id','expected_revision'},
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

    def cancellation_snapshot(self,packet):
        action=self._action(packet)
        if action not in ('cancel_tasks','cancel'):raise DomainError('Cancellation snapshot requires cancel action')
        identity=fingerprint(packet)
        with self.uow() as u:
            sid=packet['sprint_id'] if packet['sprint_id'] is not None else u.sprints.scope(self.actor)
            path_identifier(sid);record=u.sprints.get(sid)
            if record is None or record['project']!=self.project:raise DomainError('Unknown Sprint in this project')
            old=u.sprints.receipt(sid,packet['request_id'],identity)
            if old is not None:return {'receipt':old,'sprint':sid}
            s=Sprint.restore(record['aggregate'])
            whole=action=='cancel'
            requested=([task_identity(t) for t in s.plan.data['tasks']] if s.state=='published' else []) if whole else list(s.cancellation_scope(packet['tasks'],packet['mode']))
            facts=u.sprints.facts(sid)
            ids=[tid for tid in requested if facts[tid]['status'] not in ('completed','cancelled')]
            snapshots={}
            for tid in ids:
                if facts[tid]['pending'] is not None:raise DomainError('External operation outcome must be resolved first')
                task=u.tasks.load(tid)
                snapshots[tid]={'fact':facts[tid],'task_version':task.state.version}
            return {'receipt':None,'sprint':sid,'tasks':snapshots}

    def publication_preflight(self,packet,identity):
        with self.uow() as u:
            sid=packet['sprint_id'] if packet['sprint_id'] is not None else u.sprints.scope(self.actor)
            path_identifier(sid);record=u.sprints.get(sid)
            if record is None:raise DomainError('Sprint does not exist')
            if record['project']!=self.project:raise DomainError('Sprint belongs to another project')
            if u.sprints.receipt(sid,packet['request_id'],identity) is not None:return None
            if type(packet['expected_revision']) is not int or packet['expected_revision']!=record['aggregate']['revision']:
                raise VersionConflict('Publish revision changed')
            sprint=Sprint.restore(record['aggregate'])
            errors=(sprint.plan.content_errors(sprint.policy)
                    + sprint.plan.graph_errors(sprint.policy))
            if errors:raise DomainError('; '.join(errors))
            intents=deepcopy(sprint.plan.data['tasks'])
            processes=deepcopy(record['processes'])
            automatic_checks=deepcopy(record['automatic_checks'])
            snapshot=fingerprint(record)
        base=self.creation_base()
        prepared=[]
        for intent in intents:
            if isinstance(intent,str):
                with self.uow() as u:
                    member_snapshot=u.tasks.membership_snapshot(intent)
                if member_snapshot['status']!='newborn':
                    self._available_membership(member_snapshot,sprint.plan.data['id'])
                    prepared.append({
                        'creation':None,'newborn':None,'existing':member_snapshot,
                    })
                    continue
                with self.uow() as u:
                    newborn,body=u.tasks.newborn_creation(intent)
                if newborn.claimed_by not in (None,self.actor):
                    raise DomainError('Newborn Sprint member is owned by another session')
                creation=self.prepare_creation(
                    body,newborn.process,automatic_checks,base,
                    record['task_decomposition'],
                )
                if not newborn.ready:
                    raise DomainError('Newborn Sprint member is not type-ready')
                prepared.append({
                    'creation':creation,
                    'newborn':newborn,
                })
                continue
            body=intent['task'] if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
            prepared.append({'creation':self.prepare_creation(
                intent,processes[body['goal_type']],automatic_checks,base,
                record['task_decomposition'],
            ),'newborn':None})
        return {'record':snapshot,'prepared':prepared}

    def apply(self,packet,preflight=None):
        action=self._action(packet)
        identity=fingerprint(packet)
        publication = self.publication_preflight(packet,identity) if action=='publish' else None
        with self.uow() as u:
            sid=packet['sprint_id'] if packet['sprint_id'] is not None else u.sprints.scope(self.actor)
            path_identifier(sid)
            record=u.sprints.get(sid)
            allocations=[]
            if record is not None and record['project']!=self.project:raise DomainError('Sprint belongs to another project')
            old=u.sprints.receipt(sid,packet['request_id'],identity)
            if old is not None:
                u.sprints.bind(self.actor,sid)
                return old
            if action=='draft':
                expected=packet['expected_revision']
                previous_members=set()
                if record is None:
                    if expected is not None:raise VersionConflict('New sprint requires explicit null revision')
                    changes,members,adoptions=self._materialize_draft_changes(
                        u,sid,packet['changes'],self.processes,self.execution_hash
                    )
                    s=Sprint.draft(SprintPlan.create(sid,packet['template'],self.policy),self.policy)
                    plan=s.plan.apply(changes,s.policy);self._validate_dependency_membership(plan)
                    s=Sprint(plan,s.policy,s.revision,s.state,s.waivers,s.decisions)
                    record={'project':self.project,'actor':self.actor,'aggregate':s.to_dict(),
                            'processes':deepcopy(self.processes),'automatic_checks':deepcopy(self.automatic_checks),
                            'task_decomposition':deepcopy(self.decomposition_policy),
                            'execution_hash':self.execution_hash}
                else:
                    if type(expected) is not int or expected!=record['aggregate']['revision']:
                        raise VersionConflict('Draft revision changed')
                    if packet['template'] is not None:raise DomainError('Edit uses saved template/policy; explicit null required')
                    if record['aggregate']['state']!='draft':
                        raise DomainError('Published sprint draft cannot be silently rewritten')
                    previous_members={item for item in
                        Sprint.restore(record['aggregate']).plan.data['tasks']
                        if isinstance(item,str)}
                    changes,members,adoptions=self._materialize_draft_changes(
                        u,sid,packet['changes'],record['processes'],record['execution_hash']
                    )
                    s=Sprint.restore(record['aggregate']);plan=s.plan.apply(changes,s.policy)
                    self._validate_dependency_membership(plan);s=s.revise(plan)
                    record={**record,'actor':self.actor,'aggregate':s.to_dict()}
                for task_id,snapshot in adoptions.items():
                    u.tasks.change_sprint_membership(
                        task_id,None,sid,snapshot['version']
                    )
                current_members={item for item in s.plan.data['tasks'] if isinstance(item,str)}
                self._detach_draft_members(
                    u,sid,sorted(previous_members-current_members)
                )
                u.sprints.save(record,expected)
                for task_id in adoptions:
                    u.sprints.add_draft_member(sid,task_id)
            else:
                if record is None:raise DomainError('Sprint does not exist')
                s=Sprint.restore(record['aggregate']);prior=s.revision
                if action=='publish':
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Publish revision changed')
                    errors=self._errors(record,u.sprints.facts(sid))
                    if errors:raise DomainError('; '.join(errors))
                    if publication is None or fingerprint(record)!=publication['record']:
                        raise VersionConflict('Sprint changed after creation preflight')
                    s=s.publish()
                    reservations=(frozenset() if all(isinstance(item,str) for item in s.plan.data['tasks'])
                                  else creation_batch_reservations(s.plan.data['tasks'],(s.plan.data['id'],)))
                    created={}
                    for intent,item in zip(s.plan.data['tasks'],publication['prepared'],strict=True):
                        prepared=item['creation']
                        if isinstance(intent,str):
                            if item.get('existing') is not None:
                                current=u.tasks.membership_snapshot(intent)
                                self._available_membership(current,sid)
                                if current!=item['existing']:
                                    raise VersionConflict('Adopted Sprint member changed after publication preflight')
                                continue
                            newborn,contract=u.tasks.newborn_creation(intent)
                            if (not newborn.ready or prepared.source_intent!=contract
                                    or newborn!=item['newborn']):
                                raise VersionConflict('Newborn Sprint member changed after creation preflight')
                            metadata=validate_creation(
                                prepared.intent, newborn.process, record['automatic_checks'],
                                record['task_decomposition'],
                            )
                            metadata.update(sprint_id=sid,goal=contract['goal'],config_hash=record['execution_hash'])
                            if newborn.creation_request is not None:
                                metadata['creation_request']=deepcopy(newborn.creation_request)
                            u.tasks.promote_newborn(build_task(metadata,None),metadata,newborn.version)
                            prepared.requirements_gate.publish_created(
                                u,newborn.task_id,prepared.requirements_context
                            )
                            if newborn.creation_request is not None:
                                allocations.append({
                                    'request_id':newborn.creation_request['request_id'],
                                    'task_id':newborn.task_id,
                                    'replayed':False,
                                })
                            continue
                        body=intent['task'] if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
                        if prepared.source_intent!=intent:
                            raise VersionConflict('Sprint Task intent changed after creation preflight')
                        allocation,contract=create_planned_in_uow(
                            u,prepared,{'config_hash':record['execution_hash']},
                            self.task_id_policy,reservations)
                        alias=allocation.request_id if allocation.request_id is not None else contract['id']
                        created[alias]=contract
                        if allocation.receipt() is not None:allocations.append(allocation.receipt())
                    if created:
                        s=replace(s,plan=SprintPlan(rewrite_task_plan(s.plan.data,created)))
                elif action=='materialize_tasks':
                    if s.state!='draft':raise DomainError('Only a legacy Sprint draft can materialize Tasks')
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Materialization revision changed')
                    legacy=list(s.plan.data['tasks'])
                    if not legacy or any(isinstance(item,str) for item in legacy):
                        raise DomainError('Sprint draft has no legacy embedded Task definitions')
                    converted,members,adoptions=self._materialize_draft_changes(
                        u,sid,[{'kind':'upsert_tasks','tasks':legacy}],
                        record['processes'],record['execution_hash']
                    )
                    ids=list(converted[0]['tasks'])
                    plan=deepcopy(s.plan.data);plan['tasks']=sorted(ids)
                    s=replace(s,plan=SprintPlan(plan),revision=prior+1)
                elif action=='extract_tasks':
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Extraction revision changed')
                    s=s.extract_tasks(packet['task_ids'])
                    snapshots={}
                    for task_id in packet['task_ids']:
                        snapshot=u.tasks.membership_snapshot(task_id)
                        self._available_membership(snapshot,sid)
                        snapshots[task_id]=snapshot
                    for task_id,snapshot in snapshots.items():
                        u.tasks.change_sprint_membership(
                            task_id,sid,None,snapshot['version']
                        )
                elif action=='dependencies':
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Dependency revision changed')
                    s=s.update_dependencies(packet['items'],{i:f['status'] for i,f in u.sprints.facts(sid).items()},packet['reason'])
                elif action=='waive_dependencies':
                    if not isinstance(packet['decisions'],list) or not packet['decisions']:
                        raise DomainError('A nonempty explicit decision batch is required')
                    s=s.waive(packet['decisions'])
                elif action in ('cancel_tasks','cancel'):
                    whole=action=='cancel'
                    if s.state not in ('draft','published'):raise DomainError('Sprint already cancelled')
                    if not whole and s.state!='published':raise DomainError('Only published members may be cancelled')
                    draft_members=([t for t in s.plan.data['tasks'] if isinstance(t,str)]
                                   if whole and s.state=='draft' else [])
                    requested=([task_identity(t) for t in s.plan.data['tasks']] if s.state=='published' else []) if whole else list(s.cancellation_scope(packet['tasks'],packet['mode']))
                    facts=u.sprints.facts(sid)
                    ids=[tid for tid in requested if facts[tid]['status'] not in ('completed','cancelled')]
                    for tid in ids:
                        if facts[tid]['pending'] is not None:raise DomainError('External operation outcome must be resolved first')
                    for tid in ids:
                        if preflight is None or tid not in preflight['tasks']:
                            raise DomainError('Cancellation requires external resource preflight')
                        expected=preflight['tasks'][tid]
                        task=u.tasks.load(tid)
                        if task.state.version!=expected['task_version'] or facts[tid]!=expected['fact']:
                            raise VersionConflict('Task changed after cancellation preflight')
                        change=task.cancel_from_sprint(packet['reason'])
                        u.tasks.save(change,task.state.version)
                        self._save_cleanup(u,tid,expected['cleanup_pending'])
                    self._detach_draft_members(u,sid,draft_members)
                    s=s.cancel(ids,packet['reason']) if whole else s.record_task_cancellation(ids,packet['reason'])
                record={**record,'actor':self.actor,'aggregate':s.to_dict()}
                u.sprints.save(record,prior)
                if action=='publish':u.sprints.publish_members(record)
                elif action=='dependencies':u.sprints.replace_dependencies(record)
                elif action=='extract_tasks':
                    for task_id in packet['task_ids']:
                        u.sprints.remove_draft_member(sid,task_id)
            u.sprints.bind(self.actor,sid)
            result=self._describe(u,record)
            if action=='materialize_tasks':result={**result,'materialized':sorted(ids)}
            if action=='extract_tasks':result={**result,'extracted':sorted(packet['task_ids'])}
            if action in ('cancel_tasks','cancel'):
                result={**result,'cleanup':{tid:item['cleanup_result'] for tid,item in preflight['tasks'].items()}}
            if allocations:result={**result,'allocations':allocations}
            u.sprints.remember(sid,packet['request_id'],identity,result)
            return result

    def known(self,sprint_id):
        with self.uow() as u:return u.sprints.get(sprint_id) is not None

    def overview_ids(self):
        with self.uow() as u:return u.sprints.published_ids(self.project)

    def read(self,sprint_id,view):
        with self.uow() as u:
            sid=sprint_id if sprint_id is not None else u.sprints.scope(self.actor)
            if sid is None:return None
            record=u.sprints.get(sid)
            if record is None or record['project']!=self.project:raise DomainError('Unknown sprint in this project')
            if view=='history':return {'sprint':sid,'layers':u.sprints.history(sid)}
            if view=='plan':return deepcopy(record)
            if view!='current':raise DomainError('Unknown sprint view')
            return self._describe(u,record)

    def select(self,sprint_id):
        with self.uow() as u:
            record=u.sprints.get(sprint_id)
            if record is None or record['project']!=self.project:raise DomainError('Unknown sprint')
            u.sprints.bind(self.actor,sprint_id)
        return self.read(sprint_id,'current')
