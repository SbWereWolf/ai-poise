"""Task-owned verification-only reuse; existing check and ownership owners do the work."""
from copy import deepcopy
import hashlib
import json

from ..modules.foundation.errors import DomainError, VersionConflict
from ..modules.sprints.domain import Sprint
from ..modules.task_delivery.domain import MaterialUseIntent
from ..modules.foundation.errors import PoiseError
from .ownership import require_reuse_ownership
from ..modules.tasks.ports import TaskReadSet


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reusable_contract(task, context):
    contract = deepcopy(context['contract'])
    for key in ('id', 'sprint_id'):
        contract.pop(key, None)
    contract['methods'] = [entry.to_dict() for entry in task.check_registry.entries]
    contract['content_contract'] = task.content_policy.to_layers()
    return {'contract': contract, 'workflow': context['workflow'],
            'requirements_snapshot': context['requirements_snapshot'],
            'requirements_agreement': context['requirements_agreement']}


def artifact_source(records, source, context, report):
    """Only references in the accepted result are eligible for local delivery."""
    references = report.get('artifacts', [])
    registered = {row['id']: row for row in records}
    if (not isinstance(references, list) or any(
            not isinstance(ref, dict) or ref.get('id') not in registered for ref in references)):
        raise DomainError('Accepted reuse artifact is not registered to the source Task')
    ids = [ref['id'] for ref in references]
    if len(ids) != len(set(ids)):
        raise DomainError('Accepted reuse artifact set contains duplicate identities')
    owners = {'task': source.state.task_id, 'sprint': context['sprint_id']}
    records = [registered[key] for key in sorted(ids)]
    if any(row['scope'] not in owners or owners[row['scope']] is None
           or row['owner'] != owners[row['scope']] for row in records):
        raise DomainError('Accepted reuse artifact has a foreign scope or owner')
    return {'task_id': source.state.task_id, 'sprint_id': context['sprint_id'],
            'stage': report.get('artifact_stage', report['stage']), 'records': records}


def reuse_inputs(inputs, task_id, source_id):
    family = inputs.read_duplicate_family(task_id)
    if (family is None or source_id == task_id or source_id not in
            {m['task_id'] for m in family['members']}):
        raise DomainError('Reuse source must be a different explicit family member')
    if any(m['status'] == 'newborn' for m in family['members'] if m['task_id'] in (task_id, source_id)):
        raise DomainError('Reuse requires executable Task contracts')
    task, source = inputs.task(task_id), inputs.task(source_id)
    if source.state.status != 'completed' or inputs.execution(source_id) is None:
        raise DomainError('Reuse source is not a completed accepted result')
    source_execution, _ = inputs.required_execution(source_id)
    report = source_execution['last_report']
    if not isinstance(report, dict) or report.get('status') != 'completed' or not report.get('commit'):
        raise DomainError('Reuse source has no accepted commit receipt')
    context = inputs.restart_context(task_id)
    if context['sprint_id'] is not None:
        sprint, facts = inputs.sprint_eligibility(task_id)
        if sprint is None or sprint['aggregate']['state'] != 'published':
            raise DomainError('Reuse requires a published Sprint')
        view = Sprint.restore(sprint['aggregate']).work_overview(facts)
        if (task_id not in facts or any(item['task'] == task_id for item in view['blocked'])
                or task.state.status == 'available' and task_id not in view['eligible']):
            raise DomainError('Reuse Task prerequisites are not satisfied')
    own = reusable_contract(task, context)
    source_context = inputs.restart_context(source_id)
    if own != reusable_contract(source, source_context):
        raise DomainError('Reuse contract differs from the accepted source contract')
    methods = [entry.to_dict()['method'] for entry in task.check_registry.entries
               if json.loads(entry.definition)['verification_plan']['green_stages']]
    return (task, context, report['commit'], fingerprint(own), methods,
            artifact_source(inputs.artifact_records(source_id), source, source_context, report))


class DuplicateReuseCommands:
    def __init__(self, unit_of_work, workspace):
        self.uow, self.workspace = unit_of_work, workspace

    def _capture(self, task_id, source_id, actor):
        return self.uow().capture(TaskReadSet((task_id,), optional_ids=(source_id,),
            execution_ids=(task_id, source_id), actors=(actor,), family_ids=(task_id,),
            material_ids=(task_id, source_id), eligibility_ids=(task_id,)))

    def verify(self, task_id, source_task_id, actor, request_id, expected_version):
        for value in (task_id, source_task_id, actor, request_id):
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise DomainError('Reuse requires explicit identifiers')
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError('Reuse requires an exact expected version')
        intent = {'task_id':task_id, 'source_task_id':source_task_id,
                  'request_id':request_id, 'expected_version':expected_version}
        intent_digest = fingerprint(intent)
        token = None
        saved_on_entry = False
        facade = self.uow()
        try:
            inputs = self._capture(task_id, source_task_id, actor)
            task = inputs.task(task_id)
            saved = None if task.duplicate_reuse is None else json.loads(task.duplicate_reuse)
            saved_on_entry = saved is not None
            if saved is None:
                if task.state.version != expected_version:
                    raise VersionConflict('Reuse Task version changed')
            elif saved['candidate']['intent_digest'] != intent_digest:
                raise DomainError('Reuse request conflicts with the saved candidate; restart explicitly')
            current_execution = inputs.execution(task_id)
            execution = None if current_execution is None else current_execution[0]
            if saved is not None and saved['verified'] and task.state.status == 'completed':
                report = None if execution is None else execution['last_report']
                if (not isinstance(report, dict) or report.get('status') != 'completed'
                        or report.get('completion_kind') != 'duplicate_reuse'
                        or report.get('intent_digest') != intent_digest
                        or report.get('execution_key') != saved['execution_key']):
                    raise DomainError('Completed reuse has no matching accepted receipt')
                return {**deepcopy(report), 'replayed':True}
            if execution is not None and execution['pending'] is not None:
                pending = execution['pending']
                if saved is None or not isinstance(pending, dict) or pending.get('kind') not in ('worktree_setup', 'check_attempt'):
                    raise DomainError('Reuse cannot bypass a pending external outcome')
            if (execution is not None and isinstance(execution['pending'], dict)
                    and execution['pending'].get('kind') == 'check_attempt'
                    and execution['pending'].get('actor') != actor):
                raise DomainError('Pending reuse checks belong to another session; resolve their outcome first')
            task, context, commit, contract_digest, methods, artifacts_source = reuse_inputs(inputs, task_id, source_task_id)
            version = task.state.version
            if saved is None:
                with self.uow() as uow:
                    uow.require_inputs(inputs)
                    ownership = uow.ownership.preflight(actor, task_id)
                    if (ownership.task_owner not in (None, actor)
                            or inputs.ownership(actor).task_id not in (None, task_id)):
                        raise DomainError('Reuse requires a released Task or its actual current owner')
                    token = self.workspace.admit_use_in(uow, MaterialUseIntent(
                        'reuse', task_id, request_id, intent_digest), [task_id, source_task_id],
                        permanent_sources=[source_task_id])
            self.workspace.admit_material(task_id, source_task_id)
            delivery = self.workspace.plan_artifacts(task_id, context, artifacts_source)
            observed, reservation = self.workspace.prepare(task_id, context, commit, execution, saved)
            candidate = ({**observed, 'intent_digest':intent_digest, 'request':intent, 'request_id':request_id,
                          'source_task_id':source_task_id, 'source_commit':commit,
                          'contract_digest':contract_digest, 'method_ids':[m['id'] for m in methods],
                          'artifact_source':artifacts_source, 'artifact_delivery':delivery,
                          'artifact_stage':artifacts_source['stage']}
                         if saved is None else saved['candidate'])
            if (candidate['contract_digest'] != contract_digest or candidate['source_commit'] != commit
                    or candidate.get('artifact_source') != artifacts_source
                    or candidate.get('artifact_delivery') != delivery):
                raise DomainError('Reuse source, contract or delivery changed; restart explicitly')
            inputs = self._capture(task_id, source_task_id, actor)
            current, _, current_commit, current_digest, _, current_source = reuse_inputs(inputs, task_id, source_task_id)
            if (current.state.version != version or (current_commit, current_digest) != (commit, contract_digest)
                    or current_source != artifacts_source):
                raise VersionConflict('Reuse inputs changed before reservation')
            if inputs.execution(task_id) != current_execution:
                raise VersionConflict('Reuse execution changed during preparation; repeat the request')
            change = current.prepare_duplicate_reuse(actor, candidate)
            write = facade.prepare_change(inputs, change)
            with self.uow() as uow:
                ownership = uow.ownership.preflight(actor, task_id)
            required = inputs.worktree_required(task_id)
            require_reuse_ownership(inputs.ownership(actor), ownership, required, task_id, actor)
            execution_write = None
            if execution is None:
                execution_write = facade.prepare_execution(inputs, task_id, reservation)
            elif saved is None:
                fresh_execution = inputs.execution(task_id)[0]
                execution_write = facade.prepare_execution(inputs, task_id,
                    {**fresh_execution, 'last_report':None, 'publication':None})
            with self.uow() as uow:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != ownership:
                    raise VersionConflict('Reuse ownership changed before reservation')
                uow.tasks.save_prepared(write)
                if required:
                    uow.ownership.bind_worktree(actor, task_id)
                if execution is None:
                    uow.execution.create_prepared(task_id, execution_write)
                elif execution_write is not None:
                    uow.execution.save_prepared(task_id, execution_write, inputs.execution(task_id)[1])
                if token is not None:
                    self.workspace.complete_use_in(uow, token, accepted=True)
        except (PoiseError, OSError) as exc:
            recovery = facade.capture(TaskReadSet((task_id,)))
            current = recovery.task(task_id)
            reserved = None if current.duplicate_reuse is None else json.loads(current.duplicate_reuse)['candidate']
            accepted = reserved is not None and reserved['intent_digest'] == intent_digest
            if token is not None:
                with self.uow() as uow:
                    uow.require_inputs(recovery)
                    self.workspace.complete_use_in(uow, token, accepted=accepted)
            if accepted and not saved_on_entry:
                raise VersionConflict('Original reuse candidate is already reserved; repeat the original request') from exc
            raise
        self.workspace.reconcile(task_id)
        self.workspace.deliver_artifacts(task_id)
        if saved is not None and saved['verified']:
            report = self.workspace.validate_verified(task_id, methods)
            return {**report, 'replayed':True}
        tree, key, receipts = self.workspace.check(task_id, methods)
        if not all(r['passed'] and r['interpretable'] for r in receipts):
            return {'status':'checks_failed', 'task':task_id, 'checks':receipts,
                    'completion_kind':'duplicate_reuse',
                    'recovery':{'action':'restart', 'task_id':task_id,
                        'expected_version':self._version(task_id),
                        'reason':'Verify imported input, then repair this branch locally; retain failed evidence'}}
        artifacts = self.workspace.validate_artifacts(task_id)
        inputs = self._capture(task_id, source_task_id, actor)
        current, _, current_commit, current_digest, current_methods, current_source = reuse_inputs(inputs, task_id, source_task_id)
        if (current_commit != commit or current_digest != contract_digest or current_methods != methods
                or current_source != artifacts_source):
            raise VersionConflict('Reuse source changed during checks')
        change = current.mark_duplicate_reuse_verified(actor, tree, key, receipts)
        write = facade.prepare_change(inputs, change)
        report = {**candidate, 'status':'reuse_verified', 'task':task_id,
            'completion_kind':'duplicate_reuse', 'commit':candidate['head'], 'execution_key':key,
            'checks':receipts, 'replayed':False, 'stage':current.stage.stage_id,
            'iteration':current.state.iteration, 'artifacts':[{'id':a['id'], 'path':a['path']} for a in artifacts]}
        execution, version = inputs.required_execution(task_id)
        execution_write = facade.prepare_execution(inputs, task_id,
            {**execution, 'last_report':report, 'publication':None})
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            uow.artifacts.link_reused(task_id, artifacts)
            uow.execution.save_prepared(task_id, execution_write, version)
        return report

    def _version(self, task_id):
        return self.uow().capture(TaskReadSet((task_id,))).task(task_id).state.version

    def accept(self, task_id, actor):
        initial = self.uow().capture(TaskReadSet((task_id,)))
        task = initial.task(task_id)
        if task.duplicate_reuse is None:
            raise DomainError('No verified reuse result')
        candidate = json.loads(task.duplicate_reuse)['candidate']
        source_id = candidate['source_task_id']
        inputs = self._capture(task_id, source_id, actor)
        task, _, commit, contract_digest, methods, artifacts_source = reuse_inputs(inputs, task_id, source_id)
        if (candidate['source_commit'] != commit or candidate['contract_digest'] != contract_digest
                or candidate.get('artifact_source') != artifacts_source):
            raise DomainError('Reuse source, contract or delivery changed before acceptance; restart explicitly')
        task.accept_duplicate_reuse(actor)
        version = task.state.version
        report = self.workspace.validate_verified(task_id, methods)
        inputs = self._capture(task_id, source_id, actor)
        current, _, latest_commit, latest_digest, _, latest_source = reuse_inputs(inputs, task_id, source_id)
        if (current.state.version != version or (latest_commit, latest_digest) != (commit, contract_digest)
                or latest_source != artifacts_source):
            raise VersionConflict('Reuse inputs changed before acceptance')
        execution, execution_version = inputs.required_execution(task_id)
        if execution['pending'] is not None or execution['last_report'] != report:
            raise DomainError('Reuse report changed or an external outcome is pending')
        facade = self.uow()
        write = facade.prepare_change(inputs, current.accept_duplicate_reuse(actor))
        completed = {**report, 'status':'completed'}
        execution_write = facade.prepare_execution(inputs, task_id, {**execution, 'last_report':completed})
        release_tree = (inputs.worktree_required(task_id)
                        and inputs.ownership(actor).worktree_task_id == task_id)
        with self.uow() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            uow.execution.save_prepared(task_id, execution_write, execution_version)
            if release_tree:
                uow.ownership.bind_worktree(actor, None)
        return completed
