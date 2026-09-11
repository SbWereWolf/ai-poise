"""Batch planning/publication through Sprint and Task owners, one local UoW."""
from copy import deepcopy
import hashlib
import json
from ..modules.sprints.domain import SprintPolicy, SprintPlan, Sprint
from ..modules.tasks.definition import validate_creation, build_task, path_identifier
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import HarnessError, DomainError, VersionConflict


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class SprintCommands:
    def __init__(self,unit_of_work,project,actor,policy,processes,automatic_checks,execution_hash):
        self.uow=unit_of_work;self.project=project;self.actor=actor
        self.policy=SprintPolicy.parse(policy);self.processes=deepcopy(processes)
        self.automatic_checks=deepcopy(automatic_checks);self.execution_hash=execution_hash

    def _errors(self,record):
        s=Sprint.restore(record['aggregate']);errors=s.plan.content_errors(s.policy)+s.plan.graph_errors(s.policy)
        for t in s.plan.data['tasks']:
            try:
                kind=t.get('goal_type')
                if not isinstance(kind,str) or kind not in record['processes']:raise DomainError('Unknown goal_type')
                validate_creation(t,record['processes'][kind],record['automatic_checks'])
            except HarnessError as exc:errors.append(f"Task {t['id']}: {exc}")
        return errors

    def _describe(self,uow,record):
        s=Sprint.restore(record['aggregate']);sid=s.plan.data['id'];facts=uow.sprints.facts(sid)
        resumable=tuple(i for i,f in facts.items() if f['claimed_by'] is None and f['handoff_available']
                        and f['status'] in ('active','verified','accepted'))
        status=s.overview({i:f['status'] for i,f in facts.items()},resumable)
        return {**status,'resumable':list(resumable),'sprint':sid,'goal':s.plan.data['goal'],'revision':s.revision,
            'tasks':[{'id':i,**v} for i,v in facts.items()],
            'counts':{k:sum(f['status']==k for f in facts.values()) for k in ('available','active','verified','accepted','completed','cancelled')},
            'errors':self._errors(record) if s.state=='draft' else [],
            'next_work':'Исправить draft и опубликовать одним пакетом' if s.state=='draft' else
                        'Выбрать одну доступную задачу; выполнить один этап и доложить',
            'active_task':None}

    def apply(self,packet):
        shapes={'draft':{'action','sprint_id','request_id','expected_revision','template','changes'},
                'publish':{'action','sprint_id','request_id','expected_revision'},
                'dependencies':{'action','sprint_id','request_id','expected_revision','items','reason'},
                'cancel_tasks':{'action','sprint_id','request_id','tasks','mode','reason'},
                'cancel':{'action','sprint_id','request_id','reason'},
                'waive_dependencies':{'action','sprint_id','request_id','decisions'}}
        if not isinstance(packet,dict) or not isinstance(packet.get('action'),str) or packet['action'] not in shapes:
            raise DomainError('Unknown sprint action')
        exact_keys(packet,shapes[packet['action']],'sprint action');path_identifier(packet['request_id'])
        identity=fingerprint(packet)
        with self.uow() as u:
            sid=packet['sprint_id'] if packet['sprint_id'] is not None else u.sprints.scope(self.actor)
            path_identifier(sid)
            record=u.sprints.get(sid);action=packet['action']
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
                    s=s.publish()
                    for contract in s.plan.data['tasks']:
                        if u.sprints.get(contract['id']) is not None:raise DomainError('Task/sprint ID collision')
                        metadata={'contract':contract,'process':record['processes'][contract['goal_type']],
                                  'sprint_id':sid,'goal':contract['goal'],'config_hash':record['execution_hash']}
                        # Publication validates and creates complete Task aggregate, but no execution/worktree.
                        u.tasks.create(build_task(metadata,None),metadata)
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
                    requested=([t['id'] for t in s.plan.data['tasks']] if s.state=='published' else []) if whole else list(s.cancellation_scope(packet['tasks'],packet['mode']))
                    facts=u.sprints.facts(sid)
                    ids=[tid for tid in requested if facts[tid]['status'] not in ('completed','cancelled')]
                    for tid in ids:
                        if facts[tid]['pending'] is not None:raise DomainError('External operation outcome must be resolved first')
                    for tid in ids:
                        task=u.tasks.load(tid);change=task.cancel_from_sprint(packet['reason'])
                        u.tasks.save(change,task.state.version)
                    s=s.cancel(ids,packet['reason']) if whole else s.record_task_cancellation(ids,packet['reason'])
                record={**record,'actor':self.actor,'aggregate':s.to_dict()}
                u.sprints.save(record,prior)
                if action=='publish':u.sprints.publish_members(record)
                elif action=='dependencies':u.sprints.replace_dependencies(record)
            u.sprints.bind(self.actor,sid)
            result=self._describe(u,record)
            u.sprints.remember(sid,packet['request_id'],identity,result)
            return result

    def known(self,sprint_id):
        with self.uow() as u:return u.sprints.get(sprint_id) is not None

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
