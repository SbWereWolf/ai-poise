"""Declared external plans. These objects do not execute Git, commands or SQL."""
from __future__ import annotations
from dataclasses import dataclass, replace
import json
import hashlib
import re
from ..foundation.errors import DomainError
from ..verification.domain import validate_method
from ..workflow.domain import exact, text
from ..tasks.lifecycle import TaskLifecycle


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def commit_id(value):
    if not isinstance(value,str) or re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',value) is None:
        raise DomainError('Exact full Git commit ID required, not a moving reference')
    return value


class CommitMessagePolicy:
    """The configured repository rule, shared by all commit-producing routes."""

    def __init__(self, pattern):
        self.pattern = pattern

    def matches(self, message):
        return isinstance(message, str) and re.fullmatch(self.pattern, message) is not None

    def require(self, message):
        if not self.matches(message):
            raise DomainError('Сообщение коммита не соответствует правилу проекта')


@dataclass(frozen=True)
class PlanSpec:
    content: str

    @classmethod
    def parse(cls, value, max_steps):
        if not isinstance(value,dict) or value.get('kind') not in ('git_merge','commands','git_publish','data_publish'):
            raise DomainError('Explicit known action kind required')
        if type(max_steps) is not int or max_steps<=0:
            raise DomainError('An explicit positive action limit is required')
        if value['kind']=='data_publish':
            from ..catalogue.publication import DataPublication
            exact(value,{'kind','intent','body'},'data publication plan')
            DataPublication.parse(value['intent'])
            if not isinstance(value['body'],(dict,list)):
                raise DomainError('A structured reviewed body is required')
            return cls(packed(value))
        if value['kind']=='git_publish':
            exact(value,{'kind','intent','candidate'},'publication plan')
            Publication.parse(value['intent']);commit_id(value['candidate'])
            return cls(packed(value))
        if value['kind']=='git_merge':
            exact(value,{'kind','base_commit','sources'},'merge plan')
            commit_id(value['base_commit']);steps=value['sources']
            if not isinstance(steps,list):raise DomainError('sources must be a list')
            ids=[]
            for step in steps:
                exact(step,{'commit','checkpoint_message'},'merge source')
                ids.append(commit_id(step['commit']));text(step['checkpoint_message'],'checkpoint message')
        else:
            exact(value,{'kind','steps'},'command plan');steps=value['steps']
            if not isinstance(steps,list):raise DomainError('steps must be a list')
            ids=[]
            for step in steps:
                exact(step,{'id','apply','probe','probe_false_exit_codes'},'command step');ids.append(text(step['id'],'action id'))
                validate_method(step['apply'],'action apply');validate_method(step['probe'],'action probe')
                codes=step['probe_false_exit_codes']
                if not isinstance(codes,list) or not codes or any(type(c) is not int or c<0 for c in codes) or len(codes)!=len(set(codes)) or step['probe']['expected_exit_code'] in codes:
                    raise DomainError('Declare unambiguous known-false probe exit codes explicitly')
        if not steps or len(steps)>max_steps or len(set(ids))!=len(ids):
            raise DomainError('Actions require nonempty unique identities within the configured limit')
        return cls(packed(value))

    @property
    def data(self):return json.loads(self.content)
    @property
    def kind(self):return self.data['kind']
    @property
    def steps(self):
        if self.kind in ('git_publish','data_publish'):return [self.data['intent']]
        return self.data['sources'] if self.kind=='git_merge' else self.data['steps']
    @property
    def digest(self):return identity(self.data)


@dataclass(frozen=True)
class ActionBinding:
    task_id: str
    lifecycle: TaskLifecycle
    stage: str
    iteration: int

    def __post_init__(self):
        text(self.task_id, 'Task ID')
        text(self.stage, 'action stage')
        if not isinstance(self.lifecycle, TaskLifecycle):
            raise DomainError('An explicit Task lifecycle is required')
        if type(self.iteration) is not int or self.iteration <= 0:
            raise DomainError('Action iteration must be a positive integer')


@dataclass(frozen=True)
class ActionRun:
    plan: PlanSpec
    cursor: int
    status: str
    steps: tuple[str,...]
    before: str | None
    current: str | None
    version: int
    attempts: int
    binding: ActionBinding | None = None

    def __post_init__(self):
        if self.status not in ('prepared','running','awaiting_resolution','failed','blocked','complete'):
            raise DomainError('Invalid action state')
        if type(self.cursor) is not int or self.cursor!=len(self.steps) or not 0<=self.cursor<=len(self.plan.steps):
            raise DomainError('Invalid completed action prefix')
        if type(self.version) is not int or self.version<0 or type(self.attempts) is not int or self.attempts<0:
            raise DomainError('Invalid action counters')
        if (self.status=='complete') != (self.cursor==len(self.plan.steps)):
            raise DomainError('Completion does not match the completed action prefix')
        if self.status in ('running','awaiting_resolution','failed','blocked') and self.before is None:
            raise DomainError('External action has no before-state')

    @classmethod
    def new(cls,plan):return cls(plan,0,'prepared',(),None,None,0,0)

    def bind(self, binding: ActionBinding):
        if not isinstance(binding, ActionBinding):
            raise DomainError('An explicit action binding is required')
        if self.binding is not None and self.binding != binding:
            raise DomainError('Action lifecycle binding changed')
        return replace(self, binding=binding)

    @property
    def waiting_for_probe(self):return self.status=='running'

    def start(self,index,observed_before):
        if self.status!='prepared' or index!=self.cursor or index>=len(self.plan.steps):
            raise DomainError('An unknown or completed effect cannot be started again')
        return replace(self,status='running',before=packed(observed_before),current=None,version=self.version+1,attempts=self.attempts+1)

    def record(self,status,receipt):
        if self.status not in ('running','awaiting_resolution'):
            raise DomainError('No running action for receipt')
        if status not in ('ready','awaiting_resolution','failed','blocked'):
            raise DomainError('Unknown action receipt state')
        record=packed({'index':self.cursor,'before':json.loads(self.before),'result':receipt})
        if status=='ready':
            cursor=self.cursor+1
            return replace(self,cursor=cursor,status='complete' if cursor==len(self.plan.steps) else 'prepared',
                           steps=self.steps+(record,),current=None,before=None,version=self.version+1)
        return replace(self,status=status,current=packed(receipt),version=self.version+1)

    def retry_publication(self,observed_target,max_attempts):
        if self.plan.kind!='git_publish' or self.status not in ('running','blocked'):
            raise DomainError('Only a probed publication can retry')
        if observed_target!=self.plan.data['intent']['expected_commit']:
            raise DomainError('Target changed; retry must not overwrite it')
        if self.attempts>=max_attempts:
            raise DomainError('Publication attempt limit reached; user decision required')
        return replace(self,status='running',current=None,attempts=self.attempts+1,version=self.version+1)

    def to_dict(self):
        return {'plan':self.plan.data,'cursor':self.cursor,'status':self.status,
                'steps':[json.loads(s) for s in self.steps],
                'before':None if self.before is None else json.loads(self.before),
                'current':None if self.current is None else json.loads(self.current),'version':self.version,'attempts':self.attempts}

    @classmethod
    def restore(cls,value,max_steps):
        exact(value,{'plan','cursor','status','steps','before','current','version','attempts'},'action state')
        return cls(PlanSpec.parse(value['plan'],max_steps),value['cursor'],value['status'],
                   tuple(packed(s) for s in value['steps']),None if value['before'] is None else packed(value['before']),
                   None if value['current'] is None else packed(value['current']),value['version'],value['attempts'])


@dataclass(frozen=True)
class Publication:
    target_ref: str
    expected_commit: str
    authorization: str

    @classmethod
    def parse(cls,value):
        exact(value,{'target_ref','expected_commit','authorization'},'publication intent')
        ref=text(value['target_ref'],'target ref')
        if not ref.startswith('refs/heads/') or len(ref)==len('refs/heads/') or any(c in ref for c in ('\x00','\n',' ')):
            raise DomainError('An explicit full branch ref is required')
        return cls(ref,commit_id(value['expected_commit']),text(value['authorization'],'user authorization'))

    def to_dict(self):
        return {'target_ref':self.target_ref,'expected_commit':self.expected_commit,'authorization':self.authorization}


def parse_apply_work(work,max_steps):
    exact(work,{'plan','phase','resolutions','finding_resolutions'},'apply_plan stage_work')
    if not isinstance(work['finding_resolutions'],list):raise DomainError('finding_resolutions must be an explicit list')
    plan=PlanSpec.parse(work['plan'],max_steps)
    if plan.kind in ('git_publish','data_publish'):raise DomainError('Use the publication handler for target updates')
    if work['phase'] not in ('prepare','continue') or not isinstance(work['resolutions'],list):
        raise DomainError('Explicit prepare/continue phase and resolutions list required')
    paths=[]
    for r in work['resolutions']:
        exact(r,{'path','reason'},'conflict resolution')
        paths.append(text(r['path'],'conflict path'));text(r['reason'],'resolution reason')
    if len(paths)!=len(set(paths)):
        raise DomainError('Conflict resolution paths must be unique')
    if plan.kind=='commands' and (work['phase']!='prepare' or work['resolutions']):
        raise DomainError('Command plans do not accept Git conflict resolutions')
    return plan
