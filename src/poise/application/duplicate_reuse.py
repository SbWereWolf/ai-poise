"""Task-owned verification-only reuse; existing check and ownership owners do the work."""
from copy import deepcopy
import hashlib
import json

from ..modules.foundation.errors import DomainError, VersionConflict
from ..modules.sprints.domain import Sprint
from .ownership import reserve_reuse_in, release_dependent_worktree_in
from .handoff import HandoffCommands


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reusable_contract(task, context):
    contract = deepcopy(context['contract'])
    for key in ('id', 'sprint_id'):
        contract.pop(key, None)
    contract['methods'] = [entry.to_dict() for entry in task.check_registry.entries]
    contract['content_contract'] = task.content_policy.to_layers()
    return {'contract': contract, 'process': context['process'],
            'requirements_snapshot': context['requirements_snapshot'],
            'requirements_agreement': context['requirements_agreement']}


def artifact_source_in(uow, source, context, report):
    """Only references in the accepted result are eligible for local delivery."""
    references = report.get('artifacts', [])
    registered = {row['id']: row for row in uow.artifacts.records(source.state.task_id)}
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


def reuse_inputs_in(uow, task_id, source_id):
    family = uow.tasks.read_duplicate_family(task_id)
    if (family is None or source_id == task_id or source_id not in
            {m['task_id'] for m in family['members']}):
        raise DomainError('Reuse source must be a different explicit family member')
    if any(m['status'] == 'newborn' for m in family['members'] if m['task_id'] in (task_id, source_id)):
        raise DomainError('Reuse requires executable Task contracts')
    task, source = uow.tasks.load(task_id), uow.tasks.load(source_id)
    if source.state.status != 'completed' or not uow.execution.exists(source_id):
        raise DomainError('Reuse source is not a completed accepted result')
    source_execution, _ = uow.execution.load(source_id)
    report = source_execution['last_report']
    if not isinstance(report, dict) or report.get('status') != 'completed' or not report.get('commit'):
        raise DomainError('Reuse source has no accepted commit receipt')
    context = uow.tasks.restart_context(task_id)
    if context['sprint_id'] is not None:
        sprint = uow.sprints.get(context['sprint_id'])
        if sprint is None or sprint['aggregate']['state'] != 'published':
            raise DomainError('Reuse requires a published Sprint')
        facts = uow.sprints.facts(context['sprint_id'])
        view = Sprint.restore(sprint['aggregate']).work_overview(facts)
        if (task_id not in facts or any(item['task'] == task_id for item in view['blocked'])
                or task.state.status == 'available' and task_id not in view['eligible']):
            raise DomainError('Reuse Task prerequisites are not satisfied')
    own = reusable_contract(task, context)
    source_context = uow.tasks.restart_context(source_id)
    if own != reusable_contract(source, source_context):
        raise DomainError('Reuse contract differs from the accepted source contract')
    methods = [entry.to_dict()['method'] for entry in task.check_registry.entries
               if json.loads(entry.definition)['verification_plan']['green_stages']]
    return (task, context, report['commit'], fingerprint(own), methods,
            artifact_source_in(uow, source, source_context, report))


class DuplicateReuseCommands:
    def __init__(self, unit_of_work, workspace):
        self.uow, self.workspace = unit_of_work, workspace

    def verify(self, task_id, source_task_id, actor, request_id, expected_version):
        for value in (task_id, source_task_id, actor, request_id):
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise DomainError('Reuse requires explicit identifiers')
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError('Reuse requires an exact expected version')
        intent = {'task_id': task_id, 'source_task_id': source_task_id,
                  'request_id': request_id, 'expected_version': expected_version}
        intent_digest = fingerprint(intent)
        with self.uow() as uow:
            task = uow.tasks.load(task_id)
            saved = None if task.duplicate_reuse is None else json.loads(task.duplicate_reuse)
            if saved is None:
                if task.state.version != expected_version:
                    raise VersionConflict('Reuse Task version changed')
            elif saved['candidate']['intent_digest'] != intent_digest:
                raise DomainError('Reuse request conflicts with the saved candidate; restart explicitly')
            execution = uow.execution.load(task_id)[0] if uow.execution.exists(task_id) else None
            if execution is not None and execution['pending'] is not None:
                pending = execution['pending']
                if saved is None or not isinstance(pending, dict) or pending.get('kind') not in ('worktree_setup', 'check_attempt'):
                    raise DomainError('Reuse cannot bypass a pending external outcome')
            if (execution is not None and isinstance(execution['pending'], dict)
                    and execution['pending'].get('kind') == 'check_attempt'
                    and execution['pending'].get('actor') != actor):
                raise DomainError('Pending reuse checks belong to another session; resolve their outcome first')
            if saved is not None and saved['verified'] and task.state.status == 'completed':
                # Exact terminal replay returns history, not a fresh verification.
                # Neither worktree cleanup nor later Sprint cancellation invalidates it.
                report = None if execution is None else execution['last_report']
                if (not isinstance(report, dict) or report.get('status') != 'completed'
                        or report.get('completion_kind') != 'duplicate_reuse'
                        or report.get('intent_digest') != intent_digest
                        or report.get('execution_key') != saved['execution_key']):
                    raise DomainError('Completed reuse has no matching accepted receipt')
                return {**deepcopy(report), 'replayed': True}
            task, context, commit, contract_digest, methods, artifact_source = reuse_inputs_in(uow, task_id, source_task_id)
            version = task.state.version
        handoff = (self.workspace.handoff_preflight(task_id)
                   if task.state.claimed_by is None and execution is not None else None)
        delivery = self.workspace.plan_artifacts(task_id, context, artifact_source)
        observed, reservation = self.workspace.prepare(task_id, context, commit, execution, saved)
        candidate = ({**observed, 'intent_digest': intent_digest, 'request': intent, 'request_id': request_id,
                      'source_task_id': source_task_id, 'source_commit': commit,
                      'contract_digest': contract_digest, 'method_ids': [m['id'] for m in methods],
                      'artifact_source': artifact_source, 'artifact_delivery': delivery,
                      'artifact_stage': artifact_source['stage']}
                     if saved is None else saved['candidate'])
        if (candidate['contract_digest'] != contract_digest or candidate['source_commit'] != commit
                or candidate.get('artifact_source') != artifact_source
                or candidate.get('artifact_delivery') != delivery):
            raise DomainError('Reuse source, contract or delivery changed; restart explicitly')
        with self.uow() as uow:
            current, _, current_commit, current_digest, _, current_source = reuse_inputs_in(uow, task_id, source_task_id)
            if (current.state.version != version or (current_commit, current_digest) != (commit, contract_digest)
                    or current_source != artifact_source):
                raise VersionConflict('Reuse inputs changed before reservation')
            change = (current.prepare_duplicate_reuse(actor, candidate) if handoff is None
                      else HandoffCommands.resume_reuse_in(uow, current, actor, candidate, handoff))
            reserve_reuse_in(uow, current, change, actor)
            if execution is None:
                uow.execution.create(task_id, reservation)
            elif saved is None:
                uow.execution.patch(task_id, {'last_report': None, 'publication': None})
        self.workspace.reconcile(task_id)
        self.workspace.deliver_artifacts(task_id)
        if saved is not None and saved['verified']:
            report = self.workspace.validate_verified(task_id, methods)
            return {**report, 'replayed': True}
        tree, key, receipts = self.workspace.check(task_id, methods)
        if not all(r['passed'] and r['interpretable'] for r in receipts):
            return {'status': 'checks_failed', 'task': task_id, 'checks': receipts,
                    'completion_kind': 'duplicate_reuse',
                    'recovery': {'action': 'restart', 'task_id': task_id,
                        'expected_version': self._version(task_id),
                        'reason': 'Verify imported input, then repair this branch locally; retain failed evidence'}}
        artifacts = self.workspace.validate_artifacts(task_id)
        with self.uow() as uow:
            current, _, current_commit, current_digest, current_methods, current_source = reuse_inputs_in(uow, task_id, source_task_id)
            if (current_commit != commit or current_digest != contract_digest or current_methods != methods
                    or current_source != artifact_source):
                raise VersionConflict('Reuse source changed during checks')
            change = current.mark_duplicate_reuse_verified(actor, tree, key, receipts)
            uow.tasks.save(change, current.state.version)
            uow.artifacts.link_reused(task_id, artifacts)
            report = {**candidate, 'status': 'reuse_verified', 'task': task_id,
                      'completion_kind': 'duplicate_reuse', 'commit': candidate['head'],
                      'execution_key': key, 'checks': receipts, 'replayed': False,
                      'stage': current.stage.stage_id, 'iteration': current.state.iteration,
                      'artifacts': [{'id': a['id'], 'path': a['path']} for a in artifacts]}
            uow.execution.patch(task_id, {'last_report': report, 'publication': None})
        return report

    def _version(self, task_id):
        with self.uow() as uow:
            return uow.tasks.load(task_id).state.version

    def accept(self, task_id, actor):
        with self.uow() as uow:
            task = uow.tasks.load(task_id)
            if task.duplicate_reuse is None:
                raise DomainError('No verified reuse result')
            proof = json.loads(task.duplicate_reuse)
            candidate = proof['candidate']
            _, _, commit, contract_digest, methods, artifact_source = reuse_inputs_in(uow, task_id, candidate['source_task_id'])
            if (candidate['source_commit'] != commit or candidate['contract_digest'] != contract_digest
                    or candidate.get('artifact_source') != artifact_source):
                raise DomainError('Reuse source, contract or delivery changed before acceptance; restart explicitly')
            task.accept_duplicate_reuse(actor)  # Pure authorization/state preflight.
            version = task.state.version
        report = self.workspace.validate_verified(task_id, methods)
        with self.uow() as uow:
            current, _, latest_commit, latest_digest, _, latest_source = reuse_inputs_in(uow, task_id, candidate['source_task_id'])
            if (current.state.version != version or (latest_commit, latest_digest) != (commit, contract_digest)
                    or latest_source != artifact_source):
                raise VersionConflict('Reuse inputs changed before acceptance')
            execution, _ = uow.execution.load(task_id)
            if execution['pending'] is not None or execution['last_report'] != report:
                raise DomainError('Reuse report changed or an external outcome is pending')
            uow.tasks.save(current.accept_duplicate_reuse(actor), version)
            completed = {**report, 'status': 'completed'}
            uow.execution.patch(task_id, {'last_report': completed})
            release_dependent_worktree_in(uow, actor, task_id)
        return completed
