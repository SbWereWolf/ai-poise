"""Sprint owns plans and graph decisions; no workspace, SQL or test execution."""
from copy import deepcopy
from dataclasses import dataclass, replace
from collections import deque
from ..content.domain import SectionRule, SectionBook
from ..verification.domain import exact_keys
from ..tasks.definition import path_identifier
from ..tasks.allocation import creation_alias
from ..foundation.errors import DomainError


def task_identity(value):
    if isinstance(value, str):
        return path_identifier(value)
    return creation_alias(value)


@dataclass(frozen=True)
class SprintPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        exact_keys(raw,{'max_tasks','max_dependencies','acceptable_terminal_states','templates','section_rules'},'sprint policy')
        for key in ('max_tasks','max_dependencies'):
            if type(raw[key]) is not int or raw[key]<=0:raise DomainError(f'{key}: explicit positive limit required')
        states=raw['acceptable_terminal_states']
        if not isinstance(states,list) or 'completed' not in states or set(states)-{'completed','cancelled'} or len(states)!=len(set(states)):
            raise DomainError('Explicit acceptable terminal states required')
        if not isinstance(raw['section_rules'],list):raise DomainError('Explicit section_rules required')
        rules=[]
        for item in raw['section_rules']:
            exact_keys(item,{'name','template','normalization','required'},'sprint section')
            rules.append(SectionRule(**item))
        SectionBook(tuple(rules))
        if not isinstance(raw['templates'],dict) or not raw['templates']:raise DomainError('Explicit sprint templates required')
        for key,t in raw['templates'].items():
            path_identifier(key)
            exact_keys(t,{'version','goal','requirements','definition_of_done','sections','tasks','dependencies'},'sprint template')
            if not isinstance(t['version'],str) or not t['version']:raise DomainError('Template version required')
            if not isinstance(t['goal'],str) or not isinstance(t['requirements'],list) or not isinstance(t['definition_of_done'],list):
                raise DomainError('Invalid template purpose')
            if (not isinstance(t['sections'],dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in t['sections'].items())
                or {r.name for r in rules}-t['sections'].keys() or not isinstance(t['tasks'],list) or not isinstance(t['dependencies'],list)):
                raise DomainError('Invalid template content')
        return cls(deepcopy(raw))

    @property
    def rules(self):return tuple(SectionRule(**r) for r in self.data['section_rules'])


@dataclass(frozen=True)
class SprintPlan:
    data: dict

    @classmethod
    def create(cls, sprint_id, template, policy):
        path_identifier(sprint_id);exact_keys(template,{'id','version'},'template selection')
        if template['id'] not in policy.data['templates']:raise DomainError('Unknown explicit sprint template')
        t=policy.data['templates'][template['id']]
        if t['version']!=template['version']:raise DomainError('Sprint template version mismatch')
        return cls({'id':sprint_id,**deepcopy({k:v for k,v in t.items() if k!='version'})})

    def apply(self, changes, policy):
        if not isinstance(changes,list):raise DomainError('changes must be an explicit batch')
        result=deepcopy(self.data);seen=set();by_kind={}
        shapes={'purpose':{'kind','goal','requirements','definition_of_done'},'sections':{'kind','values'},
                'upsert_tasks':{'kind','tasks'},'remove_tasks':{'kind','ids'},'dependencies':{'kind','items'}}
        for change in changes:
            if not isinstance(change,dict) or not isinstance(change.get('kind'),str) or change['kind'] not in shapes:
                raise DomainError('Unknown sprint change')
            kind=change['kind'];exact_keys(change,shapes[kind],kind)
            if kind in seen:raise DomainError('Conflicting duplicate sprint change kind')
            seen.add(kind);by_kind[kind]=change
        if 'purpose' in by_kind:
            item=by_kind['purpose']
            if not isinstance(item['goal'],str):raise DomainError('goal must be text')
            for key in ('requirements','definition_of_done'):
                if not isinstance(item[key],list) or any(not isinstance(x,str) or not x.strip() for x in item[key]):
                    raise DomainError(f'{key} must be explicit text list')
            for k in ('goal','requirements','definition_of_done'):result[k]=deepcopy(item[k])
        if 'sections' in by_kind:
            v=by_kind['sections']['values']
            if not isinstance(v,dict) or any(not isinstance(k,str) or not k or not isinstance(s,str) for k,s in v.items()):
                raise DomainError('sections requires named text values')
            result['sections'].update(v)
        tasks={task_identity(t):t for t in result['tasks']}
        remove=by_kind['remove_tasks']['ids'] if 'remove_tasks' in by_kind else []
        additions=by_kind['upsert_tasks']['tasks'] if 'upsert_tasks' in by_kind else []
        if not isinstance(remove,list) or any(not isinstance(x,str) for x in remove) or len(remove)!=len(set(remove)):
            raise DomainError('remove_tasks requires unique IDs')
        if not isinstance(additions,list):raise DomainError('upsert_tasks requires task objects')
        ids=[]
        for t in additions:
            ids.append(path_identifier(task_identity(t)))
        if len(ids)!=len(set(ids)) or set(ids)&set(remove):raise DomainError('Conflicting task mutations in batch')
        for i in remove:
            if i not in tasks:raise DomainError(f'Unknown removed draft task {i}')
            del tasks[i]
        for t in additions:tasks[task_identity(t)]=deepcopy(t)
        result['tasks']=[tasks[i] for i in sorted(tasks)]
        if 'dependencies' in by_kind:
            v=by_kind['dependencies']['items']
            if not isinstance(v,list):raise DomainError('dependencies requires a list')
            for edge in v:
                exact_keys(edge,{'predecessor','successor','kind'},'dependency')
                path_identifier(edge['predecessor']);path_identifier(edge['successor'])
                if edge['kind'] not in ('completion','result'):raise DomainError('Unknown explicit dependency kind')
            result['dependencies']=sorted(deepcopy(v),key=lambda e:(e['predecessor'],e['successor']))
        if len(result['tasks'])>policy.data['max_tasks'] or len(result['dependencies'])>policy.data['max_dependencies']:
            raise DomainError('Sprint batch exceeds explicit graph limits')
        return SprintPlan(result)

    def graph_errors(self, policy):
        errors=[];ids={task_identity(t) for t in self.data['tasks']};edges=self.data['dependencies']
        for t in self.data['tasks']:
            if isinstance(t, str):
                continue
            body=t['task'] if isinstance(t,dict) and set(t)=={'request_id','task'} else t
            if body.get('sprint_id')!=self.data['id']:
                errors.append(f"Task {task_identity(t)} belongs to a different sprint or has no explicit membership")
        seen=set();adj={i:[] for i in ids};indegree=dict.fromkeys(ids,0)
        for e in edges:
            a,b=e['predecessor'],e['successor']
            if a not in ids or b not in ids:errors.append(f'Dangling dependency {a} -> {b}');continue
            if (a,b) in seen:errors.append(f'Duplicate dependency {a} -> {b}');continue
            seen.add((a,b));adj[a].append(b);indegree[b]+=1
        queue=deque(i for i in sorted(ids) if indegree[i]==0);visited=0
        while queue:
            a=queue.popleft();visited+=1
            for b in adj[a]:
                indegree[b]-=1
                if indegree[b]==0:queue.append(b)
        if visited!=len(ids):errors.append('Sprint dependencies contain a cycle')
        if len(ids)>policy.data['max_tasks'] or len(edges)>policy.data['max_dependencies']:
            errors.append('Sprint exceeds configured limits')
        return errors

    def content_errors(self, policy):
        errors=[]
        if not self.data['goal'].strip():errors.append('Sprint goal is required')
        for f in ('requirements','definition_of_done'):
            if not self.data[f] or len(self.data[f])!=len(set(self.data[f])):errors.append(f'{f} must be nonempty and unique')
        if not self.data['tasks']:errors.append('Published sprint requires tasks')
        try:
            SectionBook(policy.rules).prepare({r.name:self.data['sections'][r.name] for r in policy.rules})
        except (DomainError,KeyError) as exc:errors.append(str(exc))
        return errors


@dataclass(frozen=True)
class Sprint:
    plan: SprintPlan
    policy: SprintPolicy
    revision: int
    state: str
    waivers: tuple
    decisions: tuple

    @classmethod
    def draft(cls,plan,policy):return cls(plan,policy,1,'draft',(),())

    def add_newborn_member(self, task_id):
        if self.state != 'draft':
            raise DomainError('Newborn membership requires a Sprint draft')
        path_identifier(task_id)
        tasks = [task_identity(item) for item in self.plan.data['tasks']]
        if task_id in tasks:
            raise DomainError('Task is already a Sprint draft member')
        plan = deepcopy(self.plan.data)
        plan['tasks'] = sorted(tasks + [task_id])
        candidate = SprintPlan(plan)
        errors = candidate.graph_errors(self.policy)
        if errors:
            raise DomainError('; '.join(errors))
        return replace(self, plan=candidate, revision=self.revision + 1)

    def revise(self,plan):
        if self.state!='draft':raise DomainError('Published sprint draft cannot be silently rewritten')
        if plan.data['id']!=self.plan.data['id']:raise DomainError('Cannot change sprint identity')
        return replace(self,plan=plan,revision=self.revision+1)

    def publish(self):
        if self.state!='draft':raise DomainError('Sprint is not a draft')
        errors=self.plan.content_errors(self.policy)+self.plan.graph_errors(self.policy)
        if errors:raise DomainError('; '.join(errors))
        return replace(self,state='published',revision=self.revision+1)

    def waive(self,decisions):
        if self.state!='published':raise DomainError('Only published dependencies may be waived')
        edges={(e['predecessor'],e['successor']) for e in self.plan.data['dependencies']}
        seen=set();out=list(self.waivers)
        for d in decisions:
            exact_keys(d,{'predecessor','successor','reason'},'dependency decision')
            pair=d['predecessor'],d['successor']
            if pair not in edges or pair in seen or not isinstance(d['reason'],str) or not d['reason'].strip():
                raise DomainError('Explicit unique existing edge and user reason required')
            seen.add(pair)
            old=next((x for x in out if (x['predecessor'],x['successor'])==pair),None)
            if old is not None and old!=d:raise DomainError('Existing waiver is immutable')
            if old is None:out.append(deepcopy(d))
        return replace(self,waivers=tuple(out),revision=self.revision+1,
                       decisions=self.decisions+({'kind':'dependency_waiver','items':deepcopy(decisions)},))

    def update_dependencies(self,items,states,reason):
        if self.state!='published':raise DomainError('Dependency editing requires a published sprint')
        if not isinstance(reason,str) or not reason.strip():raise DomainError('User reason required')
        candidate=self.plan.apply([{'kind':'dependencies','items':items}],self.policy)
        errors=candidate.graph_errors(self.policy)
        if errors:raise DomainError('; '.join(errors))
        old={(e['predecessor'],e['successor'],e['kind']) for e in self.plan.data['dependencies']}
        new={(e['predecessor'],e['successor'],e['kind']) for e in candidate.data['dependencies']}
        if any(states[b]!='available' for a,b,k in old.symmetric_difference(new)):
            raise DomainError('Cannot change prerequisites of already started/completed work')
        unchanged={(a,b) for a,b,k in old.intersection(new)}
        return replace(self,plan=candidate,revision=self.revision+1,
            waivers=tuple(w for w in self.waivers if (w['predecessor'],w['successor']) in unchanged),
            decisions=self.decisions+({'kind':'dependencies_changed','reason':reason},))

    def replace_task(self,source,replacement_task,states,reason,authorization,safety):
        if self.state!='published':raise DomainError('Task replacement requires a published Sprint')
        if not isinstance(reason,str) or not reason.strip() or not isinstance(authorization,str) or not authorization.strip():
            raise DomainError('Task replacement requires explicit reason and authorization')
        tasks={t['id']:t for t in self.plan.data['tasks']}
        if source not in tasks:raise DomainError('Source Task is not a current Sprint member')
        if not isinstance(replacement_task,dict) or 'id' not in replacement_task:
            raise DomainError('Replacement Task contract is required')
        target=path_identifier(replacement_task['id'])
        if target==source or target in tasks:raise DomainError('Replacement Task ID collision')
        if replacement_task.get('sprint_id')!=self.plan.data['id']:
            raise DomainError('Replacement Task belongs to another Sprint')
        if set(states)!=set(tasks):raise DomainError('Sprint membership and Task states disagree')
        if states[source] not in ('available','active','verified','accepted'):
            raise DomainError('Only an unfinished Task can be superseded')
        if not isinstance(safety,dict) or safety.get('kind') not in ('available','caller_owned_clean','released_handoff'):
            raise DomainError('Explicit safe replacement state is required')
        for edge in self.plan.data['dependencies']:
            if edge['predecessor']==source and states[edge['successor']]!='available':
                raise DomainError('Cannot change prerequisite of an already started successor')
        plan=deepcopy(self.plan.data)
        plan['tasks']=[deepcopy(replacement_task) if t['id']==source else deepcopy(t) for t in plan['tasks']]
        plan['tasks']=sorted(plan['tasks'],key=lambda t:t['id'])
        for edge in plan['dependencies']:
            if edge['predecessor']==source:edge['predecessor']=target
            if edge['successor']==source:edge['successor']=target
        plan['dependencies']=sorted(plan['dependencies'],key=lambda e:(e['predecessor'],e['successor']))
        waivers=[]
        for item in self.waivers:
            value=deepcopy(item)
            if value['predecessor']==source:value['predecessor']=target
            if value['successor']==source:value['successor']=target
            waivers.append(value)
        candidate=SprintPlan(plan)
        errors=candidate.graph_errors(self.policy)
        if errors:raise DomainError('; '.join(errors))
        relation={'kind':'task_replacement','source':source,'replacement':target,
                  'reason':reason,'authorization':authorization,'from_revision':self.revision,
                  'to_revision':self.revision+1,'safety':deepcopy(safety)}
        return replace(self,plan=candidate,revision=self.revision+1,waivers=tuple(waivers),
                       decisions=self.decisions+(relation,))

    def cancellation_scope(self,ids,mode):
        known={task_identity(t) for t in self.plan.data['tasks']}
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or set(ids)-known:
            raise DomainError('Cancellation requires unique task members of this sprint')
        if mode not in ('single','cascade'):raise DomainError('Explicit cancellation mode required')
        selected=set(ids)
        if mode=='cascade':
            adj={i:[] for i in known}
            for e in self.plan.data['dependencies']:adj[e['predecessor']].append(e['successor'])
            q=deque(ids)
            while q:
                for nxt in adj[q.popleft()]:
                    if nxt not in selected:selected.add(nxt);q.append(nxt)
        return tuple(sorted(selected))

    def record_task_cancellation(self,ids,reason):
        if not isinstance(reason,str) or not reason.strip():raise DomainError('User instruction required')
        return replace(self,revision=self.revision+1,
                       decisions=self.decisions+({'kind':'cancel_tasks','tasks':list(ids),'reason':reason},))

    def cancel(self,ids,reason):
        if self.state not in ('draft','published'):raise DomainError('Sprint already cancelled')
        if not isinstance(reason,str) or not reason.strip():raise DomainError('User instruction required')
        return replace(self,revision=self.revision+1,state='cancelled',
                       decisions=self.decisions+({'kind':'sprint_cancel','tasks':list(ids),'reason':reason},))

    def overview(self,states,resumable):
        ids=[task_identity(t) for t in self.plan.data['tasks']]
        if self.state=='draft':return {'status':'draft','eligible':[],'blocked':[],'active':[]}
        if self.state=='cancelled':return {'status':'cancelled','eligible':[],'blocked':[],'active':[]}
        if set(ids)!=set(states):raise DomainError('Sprint membership and task states disagree')
        if not set(resumable).issubset(states) or any(states[i] not in ('active','verified','accepted') for i in resumable):
            raise DomainError('Only preserved nonterminal tasks can be resumed')
        waived={(d['predecessor'],d['successor']) for d in self.waivers}
        blocked=[];eligible=[];active=[]
        for i in ids:
            if states[i] in ('active','verified','accepted') and i not in resumable:active.append(i);continue
            if states[i] in ('completed','cancelled'):continue
            if states[i]!='available' and i not in resumable:raise DomainError('Unknown task state')
            unmet=[{'predecessor':e['predecessor'],'reason':'cancelled_dependency' if states[e['predecessor']]=='cancelled' else 'predecessor_incomplete'}
                   for e in self.plan.data['dependencies'] if e['successor']==i and (e['predecessor'],i) not in waived
                   and states[e['predecessor']]!='completed']
            if unmet:blocked.append({'task':i,'reasons':unmet})
            else:eligible.append(i)
        terminals=self.policy.data['acceptable_terminal_states']
        if all(states[i] in terminals for i in ids):status='completed'
        elif active:status='active'
        elif eligible:status='planned' if all(states[i]=='available' for i in ids) else 'active'
        else:status='blocked'
        return {'status':status,'eligible':eligible,'blocked':blocked,'active':active}

    def to_dict(self):
        return {'plan':deepcopy(self.plan.data),'policy':deepcopy(self.policy.data),'revision':self.revision,
                'state':self.state,'waivers':deepcopy(list(self.waivers)),'decisions':deepcopy(list(self.decisions))}

    @classmethod
    def restore(cls,data):
        return cls(SprintPlan(data['plan']),SprintPolicy.parse(data['policy']),data['revision'],data['state'],tuple(data['waivers']),tuple(data['decisions']))
