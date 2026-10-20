"""Pure preparation of Sprint-owned draft membership and newborn transitions."""
from copy import deepcopy
from dataclasses import replace
from ..modules.foundation.errors import DomainError, PoiseError, VersionConflict
from ..modules.tasks.newborn import NewbornTask
from ..modules.tasks.definition import path_identifier, validate_creation
from ..modules.tasks.allocation import TaskIdPolicy, materialize_contract


def require_available_membership(snapshot, expected_sprint):
    if snapshot['sprint_id'] != expected_sprint:
        raise DomainError('Task belongs to another Sprint')
    if snapshot['claimed_by'] is not None:
        raise DomainError('Membership conversion requires an unclaimed available Task')
    execution = snapshot['execution']
    if execution is not None and (execution['pending'] is not None or execution['publication'] is not None):
        raise DomainError('Task has a pending external operation')
    if execution is not None and execution['last_report'] is not None:
        raise DomainError('Task has an existing result')
    started = snapshot['status'] != 'available' or execution is not None and (
        execution['worktree'] is not None or execution['branch'] is not None
        or execution['base'] is not None or execution['entry_tree'] is not None or execution['attempts'] != 0)
    if started:
        raise DomainError('Membership conversion requires a Task that was never started and has no worktree')


class SprintTaskChanges:
    def __init__(self, facade, snapshot, actor):
        self.facade, self.snapshot, self.actor = facade, snapshot, actor
        self.newborns = {}
        self.creates, self.updates, self.memberships = [], [], []
        self.removals, self.additions, self.allocations = [], [], []
        self.facts = {}
        self.new_ids = set()
        self.config_hashes = {}
        self.membership_states = {}

    def exists(self, task_id):
        return task_id in self.newborns or (task_id in self.snapshot.inputs.tasks and self.snapshot.inputs.exists(task_id))

    def newborn(self, task_id):
        if task_id not in self.newborns:
            self.newborns[task_id] = self.snapshot.inputs.newborn(task_id)
        return self.newborns[task_id]

    def membership(self, task_id):
        if task_id in self.membership_states:
            return self.membership_states[task_id]
        if task_id in self.newborns:
            newborn = self.newborns[task_id]
            return {'task_id':task_id, 'status':'newborn', 'claimed_by':newborn.claimed_by,
                    'version':newborn.version, 'sprint_id':newborn.sprint_id, 'execution':None}
        return self.snapshot.inputs.membership_snapshot(task_id)

    def create(self, newborn, config_hash):
        metadata = self.facade.prepare_newborn_create(newborn, config_hash)
        self.creates.append((newborn, metadata))
        self.config_hashes[newborn.task_id] = config_hash
        self.new_ids.add(newborn.task_id)
        self.newborns[newborn.task_id] = newborn
        self.facts[newborn.task_id] = self.facade.newborn_member_fact(self.snapshot, newborn, metadata)

    def update(self, before, after, config_hash, event):
        metadata = (self.facade.prepare_newborn_create(after, config_hash) if after.task_id in self.new_ids
                    else self.facade.prepare_newborn(self.snapshot.inputs, after, config_hash))
        self.updates.append((after, before.version, metadata, event))
        self.config_hashes[after.task_id] = config_hash
        self.newborns[after.task_id] = after
        self.facts[after.task_id] = self.facade.newborn_member_fact(self.snapshot, after, metadata)

    def move(self, task_id, expected_sprint, target_sprint):
        current = self.membership(task_id)
        if task_id in self.facts:
            write = self.facade.prepare_membership_fact(self.facts[task_id], expected_sprint, target_sprint, current['version'])
        else:
            write = self.facade.prepare_membership(self.snapshot.inputs, task_id, expected_sprint, target_sprint, current['version'])
        self.memberships.append(write)
        self.membership_states[task_id] = {**current, 'version':current['version'] + 1, 'sprint_id':target_sprint}
        self.facts[task_id] = self.facade.membership_member_fact(self.snapshot, write)

    def detach(self, sid, task_ids):
        for task_id in task_ids:
            current = self.membership(task_id)
            if current['status'] == 'newborn':
                newborn = self.newborn(task_id)
                if newborn.restart_history:
                    raise DomainError('Started member cannot be removed through draft membership conversion')
                if newborn.sprint_id != sid:
                    raise VersionConflict('Newborn Task Sprint membership changed')
                config_hash = (self.config_hashes[task_id] if task_id in self.config_hashes else
                               self.snapshot.inputs.restart_context(task_id)['config_hash'])
                self.update(newborn, newborn.detach_from_sprint(), config_hash, 'sprint_detached')
            else:
                require_available_membership(current, sid)
                self.move(task_id, sid, None)
            self.removals.append(task_id)

    def save(self, uow):
        for newborn, metadata in self.creates:
            uow.tasks.create_newborn_prepared(newborn, metadata)
        for newborn, version, metadata, event in self.updates:
            uow.tasks.save_newborn_prepared(newborn, version, metadata, event)
        for membership in self.memberships:
            uow.tasks.change_prepared_membership(membership)


def prepare_draft_changes(changes, sid, workflows, execution_hash, automatic_checks,
                          decomposition_policy, task_id_policy, prepared, namespace):
    from .sprints import _creation_definition
    from ..modules.verification.domain import exact_keys
    changes = deepcopy(changes)
    adoption = None
    for change in changes:
        if not isinstance(change, dict) or change.get('kind') != 'adopt_tasks':
            continue
        if adoption is not None:
            raise DomainError('Conflicting duplicate sprint change kind')
        exact_keys(change, {'kind','ids'}, 'adopt_tasks')
        ids = change['ids']
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids))):
            raise DomainError('adopt_tasks requires unique Task IDs')
        adoption = list(ids)
    adoptions = {}
    if adoption is not None:
        for task_id in adoption:
            path_identifier(task_id)
            current = prepared.membership(task_id)
            require_available_membership(current, None)
            adoptions[task_id] = current
        changes = [c for c in changes if c.get('kind') != 'adopt_tasks']
        upsert = next((c for c in changes if c.get('kind') == 'upsert_tasks'), None)
        if upsert is None:
            changes.append({'kind':'upsert_tasks', 'tasks':list(adoption)})
        else:
            upsert['tasks'] = [*upsert['tasks'], *adoption]
    aliases = {}
    for change in changes:
        if change.get('kind') != 'upsert_tasks':
            continue
        normalized = []
        for intent in change['tasks']:
            if isinstance(intent, str):
                if intent not in adoptions and prepared.newborn(intent).sprint_id != sid:
                    raise DomainError('Newborn Task belongs to another Sprint')
                aliases[intent] = intent
                normalized.append(intent)
                continue
            if isinstance(intent, dict) and set(intent) == {'request_id','task'}:
                allocation = namespace.allocate(intent, TaskIdPolicy.parse(task_id_policy), (sid, *normalized))
                prepared.allocations.append((allocation, namespace.creation_receipt(allocation.request_id)))
                contract, creation_request = materialize_contract(intent, allocation.task_id)
                alias = allocation.request_id
                if allocation.replayed and allocation.task_id not in prepared.snapshot.inputs.tasks:
                    raise VersionConflict('Draft creation receipt changed; repeat the request')
            else:
                contract, creation_request = deepcopy(intent), None
                alias = contract.get('id') if isinstance(contract, dict) else None
            if not isinstance(contract, dict) or not isinstance(contract.get('id'), str):
                raise DomainError('Sprint draft Task requires an explicit identity')
            task_id = path_identifier(contract['id'])
            draft = {k:deepcopy(v) for k,v in contract.items() if k not in ('id','sprint_id')}
            if prepared.exists(task_id):
                if prepared.membership(task_id)['status'] != 'newborn':
                    aliases[alias] = task_id
                    normalized.append(task_id)
                    continue
                newborn = prepared.newborn(task_id)
                if newborn.sprint_id != sid:
                    raise DomainError('Newborn Task belongs to another Sprint')
                if creation_request is not None and newborn.creation_request != creation_request:
                    raise DomainError('Creation request belongs to another newborn Task')
                updates = {k:v for k,v in draft.items() if newborn.draft.get(k) != v}
                if updates:
                    if newborn.claimed_by not in (None, prepared.actor):
                        raise DomainError('Newborn Task is owned by another session')
                    changed = replace(newborn.materialize_draft(updates, workflows, prepared.actor), claimed_by=newborn.claimed_by)
                    prepared.update(newborn, changed, execution_hash, 'newborn_edited')
                    newborn = changed
            else:
                newborn = replace(NewbornTask.create(task_id, sid, None), creation_request=deepcopy(creation_request))
                prepared.create(newborn, execution_hash)
                if draft:
                    changed = replace(newborn.materialize_draft(draft, workflows, prepared.actor), claimed_by=None)
                    prepared.update(newborn, changed, execution_hash, 'newborn_edited')
                    newborn = changed
            try:
                workflow = newborn.workflow
                if workflow is None:
                    raise DomainError('Unknown goal_type')
                body = {'id':task_id, 'sprint_id':sid, **deepcopy(newborn.draft)}
                validate_creation(_creation_definition(body), workflow, automatic_checks, decomposition_policy)
            except PoiseError:
                pass
            else:
                if not newborn.ready:
                    ready = newborn.mark_ready()
                    prepared.update(newborn, ready, execution_hash, 'newborn_ready')
                    newborn = ready
            normalized.append(task_id)
            aliases[alias] = task_id
        change['tasks'] = normalized
    for change in changes:
        if change.get('kind') == 'dependencies':
            change['items'] = [{**edge, 'predecessor':aliases.get(edge['predecessor'], edge['predecessor']),
                               'successor':aliases.get(edge['successor'], edge['successor'])} for edge in change['items']]
    return changes, adoptions
