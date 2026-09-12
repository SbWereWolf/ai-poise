"""Batch planning/publication through Sprint and Task owners, one local UoW."""
from copy import deepcopy
import hashlib
import json
from dataclasses import replace
from ..modules.sprints.domain import SprintPolicy, SprintPlan, Sprint
from ..modules.tasks.definition import build_task, path_identifier, validate_creation
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import PoiseError, DomainError, VersionConflict
from .tasks import (create_planned_in_uow, creation_batch_reservations, creation_intent_alias,
                    rewrite_task_plan, validate_creation_intent)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class SprintCommands:
    def __init__(self,unit_of_work,project,actor,policy,task_id_policy,processes,
                 automatic_checks,execution_hash,prepare_creation,creation_base):
        self.uow=unit_of_work;self.project=project;self.actor=actor
        self.policy=SprintPolicy.parse(policy);self.processes=deepcopy(processes)
        self.task_id_policy=task_id_policy
        self.automatic_checks=deepcopy(automatic_checks);self.execution_hash=execution_hash
        self.prepare_creation=prepare_creation;self.creation_base=creation_base

    def _errors(self,record):
        s=Sprint.restore(record['aggregate']);errors=s.plan.content_errors(s.policy)+s.plan.graph_errors(s.policy)
        for t in s.plan.data['tasks']:
            try:
                body=t['task'] if isinstance(t,dict) and set(t)=={'request_id','task'} else t
                kind=body.get('goal_type')
                if not isinstance(kind,str) or kind not in record['processes']:raise DomainError('Unknown goal_type')
                validate_creation_intent(t,record['processes'][kind],record['automatic_checks'])
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

    def _describe(self,uow,record):
        s=Sprint.restore(record['aggregate']);sid=s.plan.data['id'];facts=uow.sprints.facts(sid)
        resumable=tuple(i for i,f in facts.items() if f['claimed_by'] is None and f['handoff_available']
                        and f['status'] in ('active','verified','accepted'))
        status=s.overview({i:f['status'] for i,f in facts.items()},resumable)
        replacements=[{k:v for k,v in d.items() if k!='kind'} for d in s.decisions if d.get('kind')=='task_replacement']
        return {**status,'resumable':list(resumable),'sprint':sid,'goal':s.plan.data['goal'],'revision':s.revision,
            'replacements':replacements,
            'tasks':[{'id':i,**v} for i,v in facts.items()],
            'counts':{k:sum(f['status']==k for f in facts.values()) for k in ('available','active','verified','accepted','completed','cancelled')},
            'errors':self._errors(record) if s.state=='draft' else [],
            'next_work':'Исправить draft и опубликовать одним пакетом' if s.state=='draft' else
                        'Выбрать одну доступную задачу; выполнить один этап и доложить',
            'active_task':None}

    @staticmethod
    def _action(packet):
        shapes={'draft':{'action','sprint_id','request_id','expected_revision','template','changes'},
                'publish':{'action','sprint_id','request_id','expected_revision'},
                'dependencies':{'action','sprint_id','request_id','expected_revision','items','reason'},
                'cancel_tasks':{'action','sprint_id','request_id','tasks','mode','reason'},
                'cancel':{'action','sprint_id','request_id','reason'},
                'waive_dependencies':{'action','sprint_id','request_id','decisions'},
                'replace_task':{'action','sprint_id','request_id','expected_revision','source_task','replacement','reason','authorization'}}
        if not isinstance(packet,dict) or not isinstance(packet.get('action'),str) or packet['action'] not in shapes:
            raise DomainError('Unknown sprint action')
        exact_keys(packet,shapes[packet['action']],'sprint action');path_identifier(packet['request_id'])
        return packet['action']

    def replacement_snapshot(self,packet):
        if self._action(packet)!='replace_task':raise DomainError('Replacement snapshot requires replace_task')
        identity=fingerprint(packet)
        with self.uow() as u:
            sid=packet['sprint_id'] if packet['sprint_id'] is not None else u.sprints.scope(self.actor)
            path_identifier(sid);record=u.sprints.get(sid)
            if record is None or record['project']!=self.project:raise DomainError('Unknown Sprint in this project')
            old=u.sprints.receipt(sid,packet['request_id'],identity)
            if old is not None:return {'receipt':old,'sprint':sid}
            if type(packet['expected_revision']) is not int or packet['expected_revision']!=record['aggregate']['revision']:
                raise VersionConflict('Replacement revision changed')
            facts=u.sprints.facts(sid);source=packet['source_task'];path_identifier(source)
            if source not in facts:raise DomainError('Source Task is not a current Sprint member')
            task=u.tasks.load(source)
            return {'receipt':None,'sprint':sid,'fact':facts[source],'task_version':task.state.version,
                    'handoff':u.handoffs.latest(source)}

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
            requested=([t['id'] for t in s.plan.data['tasks']] if s.state=='published' else []) if whole else list(s.cancellation_scope(packet['tasks'],packet['mode']))
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
            errors=self._errors(record)
            if errors:raise DomainError('; '.join(errors))
            intents=deepcopy(Sprint.restore(record['aggregate']).plan.data['tasks'])
            processes=deepcopy(record['processes'])
            automatic_checks=deepcopy(record['automatic_checks'])
            snapshot=fingerprint(record)
        base=self.creation_base()
        prepared=[]
        for intent in intents:
            body=intent['task'] if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
            prepared.append(self.prepare_creation(
                intent,processes[body['goal_type']],automatic_checks,base
            ))
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
                if record is None:
                    if expected is not None:raise VersionConflict('New sprint requires explicit null revision')
                    s=Sprint.draft(SprintPlan.create(sid,packet['template'],self.policy),self.policy)
                    s=Sprint(s.plan.apply(packet['changes'],s.policy),s.policy,s.revision,s.state,s.waivers,s.decisions)
                    record={'project':self.project,'actor':self.actor,'aggregate':s.to_dict(),
                            'processes':deepcopy(self.processes),'automatic_checks':deepcopy(self.automatic_checks),
                            'execution_hash':self.execution_hash}
                else:
                    if type(expected) is not int or expected!=record['aggregate']['revision']:
                        raise VersionConflict('Draft revision changed')
                    if packet['template'] is not None:raise DomainError('Edit uses saved template/policy; explicit null required')
                    s=Sprint.restore(record['aggregate']);s=s.revise(s.plan.apply(packet['changes'],s.policy))
                    record={**record,'actor':self.actor,'aggregate':s.to_dict()}
                u.sprints.save(record,expected)
            else:
                if record is None:raise DomainError('Sprint does not exist')
                s=Sprint.restore(record['aggregate']);prior=s.revision
                if action=='publish':
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Publish revision changed')
                    errors=self._errors(record)
                    if errors:raise DomainError('; '.join(errors))
                    if publication is None or fingerprint(record)!=publication['record']:
                        raise VersionConflict('Sprint changed after creation preflight')
                    s=s.publish()
                    reservations=creation_batch_reservations(
                        s.plan.data['tasks'],(s.plan.data['id'],)
                    )
                    created={}
                    for intent,prepared in zip(s.plan.data['tasks'],publication['prepared'],strict=True):
                        body=intent['task'] if isinstance(intent,dict) and set(intent)=={'request_id','task'} else intent
                        if prepared.intent!=intent:
                            raise VersionConflict('Sprint Task intent changed after creation preflight')
                        allocation,contract=create_planned_in_uow(
                            u,prepared,{'config_hash':record['execution_hash']},
                            self.task_id_policy,reservations)
                        alias=allocation.request_id if allocation.request_id is not None else contract['id']
                        created[alias]=contract
                        if allocation.receipt() is not None:allocations.append(allocation.receipt())
                    s=replace(s,plan=SprintPlan(rewrite_task_plan(s.plan.data,created)))
                elif action=='dependencies':
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Dependency revision changed')
                    s=s.update_dependencies(packet['items'],{i:f['status'] for i,f in u.sprints.facts(sid).items()},packet['reason'])
                elif action=='replace_task':
                    if preflight is None:raise DomainError('Replacement requires external safety preflight')
                    if type(packet['expected_revision']) is not int or packet['expected_revision']!=prior:
                        raise VersionConflict('Replacement revision changed')
                    facts=u.sprints.facts(sid);source=packet['source_task'];candidate=packet['replacement']
                    if source not in facts:raise DomainError('Source Task is not a current Sprint member')
                    source_task=u.tasks.load(source)
                    if source_task.state.version!=preflight['task_version'] or facts[source]!=preflight['fact'] or u.handoffs.latest(source)!=preflight['handoff']:
                        raise VersionConflict('Source Task changed after replacement preflight')
                    kind=candidate.get('goal_type') if isinstance(candidate,dict) else None
                    if not isinstance(kind,str) or kind not in record['processes']:raise DomainError('Unknown goal_type')
                    validate_creation(candidate,record['processes'][kind],record['automatic_checks'])
                    target=candidate['id']
                    if u.tasks.exists(target) or u.sprints.get(target) is not None:raise DomainError('Replacement Task ID collision')
                    s=s.replace_task(source,candidate,{i:f['status'] for i,f in facts.items()},packet['reason'],
                                     packet['authorization'],preflight['safety'])
                    metadata={'contract':deepcopy(candidate),'process':deepcopy(record['processes'][kind]),
                              'sprint_id':sid,'goal':candidate['goal'],'config_hash':record['execution_hash']}
                    u.tasks.create(build_task(metadata,None),metadata)
                    u.tasks.save(source_task.supersede(self.actor,packet['reason']),source_task.state.version)
                    self._save_cleanup(u,source,preflight['cleanup_pending'])
                elif action=='waive_dependencies':
                    if not isinstance(packet['decisions'],list) or not packet['decisions']:
                        raise DomainError('A nonempty explicit decision batch is required')
                    s=s.waive(packet['decisions'])
                elif action in ('cancel_tasks','cancel'):
                    whole=action=='cancel'
                    if s.state not in ('draft','published'):raise DomainError('Sprint already cancelled')
                    if not whole and s.state!='published':raise DomainError('Only published members may be cancelled')
                    requested=([t['id'] for t in s.plan.data['tasks']] if s.state=='published' else []) if whole else list(s.cancellation_scope(packet['tasks'],packet['mode']))
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
                    s=s.cancel(ids,packet['reason']) if whole else s.record_task_cancellation(ids,packet['reason'])
                record={**record,'actor':self.actor,'aggregate':s.to_dict()}
                u.sprints.save(record,prior)
                if action=='publish':u.sprints.publish_members(record)
                elif action=='dependencies':u.sprints.replace_dependencies(record)
                elif action=='replace_task':
                    u.sprints.add_replacement_member(record,packet['replacement']['id'])
                    if preflight['fact']['claimed_by']==self.actor:u.handoffs.unbind(self.actor,packet['source_task'])
            u.sprints.bind(self.actor,sid)
            result=self._describe(u,record)
            if action=='replace_task':result={**result,'cleanup':{packet['source_task']:preflight['cleanup_result']}}
            elif action in ('cancel_tasks','cancel'):
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
