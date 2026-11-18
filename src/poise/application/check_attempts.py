"""Short durable boundaries around external checks; never execute or retry effects."""
from copy import deepcopy
from dataclasses import replace
from math import isfinite
from uuid import UUID, uuid4
from ..common import digest

from ..modules.foundation.errors import DomainError, PoiseError
from ..modules.tasks.domain import TaskStatus
from ..modules.tasks.ports import TaskReadSet
from .evidence import check_receipts_by_id


def is_check_attempt(value):
    return isinstance(value, dict) and value.get('kind') == 'check_attempt'


def pending_retry(value):
    if is_check_attempt(value) and value.get('version') == 3:
        descriptor = value.get('retry')
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get('request_id'), str):
            raise DomainError('Invalid pending verification retry descriptor')
        return descriptor
    return None


def retry_continuation(retry):
    return (f"Pending verification retry request_id={retry['request_id']}; "
            'resume with exact explicit verify retry, original result, failed_check_id and reason')


class PendingVerificationRetry(PoiseError):
    """Ordinary verification cannot resume an explicit retry reservation."""


def validate_retry_budget(budget, method_id):
    fields = {'method', 'prior_receipt', 'profile', 'selection', 'evidence_ids', 'cleanup_seconds'}
    if (not isinstance(budget, dict) or set(budget) != fields or budget['method'] != method_id
            or not isinstance(budget['prior_receipt'], str) or not budget['prior_receipt']
            or not isinstance(budget['profile'], dict) or budget['profile'].get('workload') != method_id
            or not isinstance(budget['selection'], dict) or not isinstance(budget['evidence_ids'], list)
            or not all(isinstance(item, str) for item in budget['evidence_ids'])):
        raise DomainError('Invalid frozen verification retry budget')
    for name, value in [('cleanup_seconds', budget['cleanup_seconds']), *(
            (name, budget['selection'].get(name)) for name in ('seconds', 'progress_gap_seconds', 'poll_seconds'))]:
        if name == 'progress_gap_seconds' and value is None and name in budget['selection']:
            continue
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise DomainError(f'Invalid frozen verification retry bound: {name}')
    return budget


def validate_identity(identity):
    if (not isinstance(identity, dict) or set(identity) != {'source', 'definition', 'environment'}
            or any(not isinstance(value, str) or len(value) != 64
                   or any(c not in '0123456789abcdef' for c in value)
                   for value in identity.values())):
        raise DomainError('Invalid check attempt component identity')
    return identity


def require_identity(attempt, identity):
    """Component facts, never environment values, explain a changed invocation."""
    if attempt.get('version') not in (2, 3):
        return  # v1 has no component facts; its exact original key remains required.
    original = validate_identity(attempt.get('identity'))
    current = validate_identity(identity)
    changed = [component for component in ('source', 'definition', 'environment')
               if original[component] != current[component]]
    if changed:
        raise DomainError('Check attempt identity changed: ' +
                          ', '.join('component=' + component for component in changed))


def validate_termination(attempt, run):
    proof = run['termination']
    if proof is None:
        return
    expected = {key: attempt[key] for key in (
        'task_id', 'actor', 'attempt_id', 'stage', 'iteration',
        'submission_digest', 'execution_key')}
    expected.update(schema='check-run-termination-1', run_id=run['run_id'],
                    tree=attempt['verified_tree'])
    fields = set(expected) | {'actual_exit_code', 'child_reaped', 'process_group_stopped', 'capture_complete'}
    if (not run['started'] or not isinstance(proof, dict) or set(proof) != fields
            or any(proof.get(key) != value for key, value in expected.items())
            or type(proof.get('iteration')) is not int or proof['iteration'] < 1
            or type(proof.get('actual_exit_code')) is not int
            or any(type(proof.get(key)) is not bool
                   for key in ('child_reaped', 'process_group_stopped', 'capture_complete'))):
        raise DomainError('Invalid check termination proof identity or metadata')


def require_quiescence(attempt):
    """A caller assertion and an empty fresh runner map cannot establish stop."""
    if attempt['version'] not in (2, 3):
        raise DomainError('Historical protocol has no authoritative termination proof')
    for run in attempt['runs']:
        validate_termination(attempt, run)
        if run['started'] and (run['termination'] is None or any(
                run['termination'][key] is not True
                for key in ('child_reaped', 'process_group_stopped', 'capture_complete'))):
            raise DomainError('Run may still be active; authoritative termination/quiescence proof required')


def validate_preserved_attempt_in(uow, task, actor, attempt):
    return validate_preserved_attempt(uow.tasks, task, actor, attempt)


def validate_preserved_attempt(source, task, actor, attempt):
    task._owned(actor)
    original_version = attempt.get('task_version')
    if (type(original_version) is not int or original_version < 0
            or original_version > task.state.version
            or (original_version == task.state.version and attempt.get('actor') != actor)
            or (original_version != task.state.version and not source.ownership_event_suffix(
                task.state.task_id, original_version, task.state.version))):
        raise DomainError('Check attempt context changed or protocol is unsupported; drift cause undetermined')
    original = replace(task, state=replace(
        task.state, version=original_version, claimed_by=attempt.get('actor')))
    validate_attempt(original, attempt.get('actor'), attempt,
                     attempt.get('verified_tree'), attempt.get('execution_key'))
    require_quiescence(attempt)


def validate_attempt(task, actor, attempt, tree, execution_key, methods=None):
    """Reject drift or unsupported persisted JSON before granting any run permission."""
    task._owned(actor)
    if task.duplicate_reuse is not None and task.state.claimed_by != actor:
        raise DomainError('Reuse checks require their current owner')
    expected = {
        'kind': 'check_attempt', 'task_id': task.state.task_id,
        'task_version': task.state.version, 'actor': actor,
        'stage': task.stage.stage_id, 'iteration': task.state.iteration,
        'submission_digest': task.check_candidate_digest,
        'verified_tree': tree, 'execution_key': execution_key,
    }
    if (task.state.status != TaskStatus.ACTIVE or task.check_candidate_digest is None
            or not isinstance(attempt, dict)
            or type(attempt.get('version')) is not int
            or attempt.get('version') not in (1, 2, 3)
            or set(attempt) != set(expected) | {'version', 'attempt_id', 'runs'} |
                ({'identity'} if attempt.get('version') in (2, 3) else set()) |
                ({'retry'} if attempt.get('version') == 3 else set())
            or any(attempt.get(key) != value for key, value in expected.items())):
        raise DomainError('Check attempt context changed or protocol is unsupported; drift cause undetermined')
    if (type(attempt['iteration']) is not int or attempt['iteration'] < 1
            or type(attempt['task_version']) is not int or attempt['task_version'] < 0):
        raise DomainError('Invalid check attempt iteration identity')
    if attempt['version'] in (2, 3):
        validate_identity(attempt['identity'])
    try:
        UUID(attempt['attempt_id'])
        runs = attempt['runs']
        if not isinstance(runs, list):
            raise ValueError('runs')
        unstarted = False
        for run in runs:
            fields = {'run_id', 'method_id', 'started'} | ({'termination'} if attempt['version'] in (2, 3) else set())
            if (not isinstance(run, dict) or set(run) != fields
                    or type(run['started']) is not bool
                    or not isinstance(run['method_id'], str) or not run['method_id']):
                raise ValueError('run descriptor')
            UUID(run['run_id'])
            if unstarted and run['started']:
                raise ValueError('nonsequential start permissions')
            unstarted = unstarted or not run['started']
            if attempt['version'] in (2, 3):
                validate_termination(attempt, run)
        if len({run['run_id'] for run in runs}) != len(runs):
            raise ValueError('duplicate run identity')
        if methods is not None and [run['method_id'] for run in runs] != list(methods):
            raise ValueError('method order')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DomainError('Invalid check attempt run identities or order') from exc
    retry = pending_retry(attempt)
    if retry is not None:
        if (set(retry) != {'request_id', 'intent', 'budgets'} or not retry['request_id']
                or not isinstance(retry['intent'], str) or len(retry['intent']) != 64
                or not isinstance(retry['budgets'], list) or len(retry['budgets']) != len(runs)):
            raise DomainError('Invalid verification retry reservation')
        for run, budget in zip(runs, retry['budgets'], strict=True):
            validate_retry_budget(budget, run['method_id'])
    return attempt


def receipt_matches(attempt, run, receipt):
    return isinstance(receipt, dict) and all(receipt.get(key) == value for key, value in {
        'id': run['run_id'], 'method': run['method_id'],
        'attempt_id': attempt['attempt_id'],
        'submission_digest': attempt['submission_digest'],
        'execution_key': attempt['execution_key'], 'tree': attempt['verified_tree'],
    }.items())


class CheckAttempts:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def _save_execution(self, facade, inputs, task_id, execution, version):
        prepared = facade.prepare_execution(inputs, task_id, execution)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.execution.save_prepared(task_id, prepared, version)

    def begin(self, task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest, identity,
              *, retry=None):
        validate_identity(identity)
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        if (task.state.version != expected_version
                or task.check_candidate_digest != submission_digest):
            raise DomainError('Check candidate changed before attempt reservation')
        if execution['pending'] is not None:
            raise DomainError('An external attempt is already pending')
        if execution['attempts'] >= limit:
            raise DomainError('Достигнут явный лимит verify_attempts; требуется решение пользователя')
        attempt = {
            'kind': 'check_attempt', 'version': 2, 'attempt_id': str(uuid4()),
            'task_id': task_id, 'task_version': task.state.version, 'actor': actor,
            'stage': task.stage.stage_id, 'iteration': task.state.iteration,
            'submission_digest': task.check_candidate_digest,
            'verified_tree': tree, 'execution_key': execution_key,
            'identity': deepcopy(identity),
            'runs': [{'run_id': str(uuid4()), 'method_id': method, 'started': False, 'termination': None}
                     for method in methods],
        }
        if retry is not None:
            previous = task.evidence_book.failed_batch(
                task.stage.stage_id, task.state.iteration, submission_digest, tree, execution_key)
            if previous is None or previous['id'] != retry['predecessor']:
                raise DomainError('Current failed verification batch changed before reservation')
            attempt.update(version=3, retry={key: retry[key] for key in ('request_id', 'intent', 'budgets')})
        validate_attempt(task, actor, attempt, tree, execution_key, methods)
        state = {
            **execution, 'pending': attempt, 'publication': None,
            'attempts': execution['attempts'] + 1,
        }
        if retry is None:
            self._save_execution(facade, inputs, task_id, state, version)
        else:
            mapping = {key: attempt[key] for key in ('actor', 'stage', 'iteration', 'submission_digest',
                                                     'verified_tree', 'execution_key', 'attempt_id')}
            mapping.update(attempt=state['attempts'], runs=[
                run['run_id'] for run in attempt['runs']
            ], predecessor=retry['predecessor'], commit=retry['commit'], packet=retry['packet'],
                budget_digest=digest(retry['budgets']))
            record = facade.prepare_action_receipt(retry['request_id'], retry['intent'], mapping)
            prepared = facade.prepare_execution(inputs, task_id, state)
            with self.unit_of_work() as uow:
                if uow.tasks.action_receipt_raw(task_id, retry['request_id'], retry['intent']) is not None:
                    raise DomainError('Retry request already reserved; submit the exact request again')
                uow.require_inputs(inputs)
                uow.execution.save_prepared(task_id, prepared, version)
                uow.tasks.remember_prepared_action(task_id, actor, record)
        return deepcopy(attempt)

    def current(self, task_id, actor, tree, execution_key, methods):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, _ = inputs.required_execution(task_id)
        return deepcopy(validate_attempt(
            task, actor, execution['pending'], tree, execution_key, methods))

    def start_run(self, task_id, actor, expected, run_id):
        """A committed started bit permits one caller, not an inferred retry."""
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), evidence_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        current = validate_attempt(task, actor, execution['pending'],
                                   expected['verified_tree'], expected['execution_key'])
        if current != expected:
            raise DomainError('Check attempt changed before run permission')
        if current['version'] not in (2, 3):
            raise DomainError('Historical check protocol grants no new run permission')
        records = check_receipts_by_id(inputs.evidence(task_id))
        for run in current['runs']:
            if run['run_id'] == run_id:
                if run['started'] or run_id in records:
                    raise DomainError(f'Unknown or already started check run {run_id}')
                run['started'] = True
                self._save_execution(facade, inputs, task_id, {**execution, 'pending': current}, version)
                return deepcopy(current)
            if not run['started'] or not receipt_matches(current, run, records.get(run['run_id'])):
                raise DomainError('Previous check run is not durably finished')
        raise DomainError('Run is not in the reserved check attempt')

    def record_termination(self, task_id, actor, expected, run_id, outcome):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        current = validate_attempt(task, actor, execution['pending'],
                                   expected['verified_tree'], expected['execution_key'])
        if current['version'] not in (2, 3):
            raise DomainError('Check attempt changed before termination proof')
        terminal = outcome.get('termination')
        if (not isinstance(terminal, dict)
                or set(terminal) != {'schema', 'child_reaped', 'process_group_stopped', 'capture_complete'}
                or terminal['schema'] != 'run-termination-1'
                or type(outcome.get('actual_exit_code')) is not int):
            raise DomainError('Actual runner termination metadata required')
        for index, run in enumerate(current['runs']):
            if run['run_id'] != run_id:
                continue
            proof = {key: current[key] for key in (
                'task_id', 'actor', 'attempt_id', 'stage', 'iteration',
                'submission_digest', 'execution_key')}
            proof.update(schema='check-run-termination-1', run_id=run_id,
                         tree=current['verified_tree'], actual_exit_code=outcome['actual_exit_code'])
            proof.update({key: terminal[key] for key in (
                'child_reaped', 'process_group_stopped', 'capture_complete')})
            candidate = deepcopy(expected)
            candidate['runs'][index]['termination'] = run['termination']
            if candidate != current:
                raise DomainError('Check attempt changed before termination proof')
            if run['termination'] is not None and run['termination'] != proof:
                raise DomainError('Cannot replace durable termination proof')
            if run['termination'] == proof:
                return deepcopy(current)
            run['termination'] = proof
            validate_termination(current, run)
            self._save_execution(facade, inputs, task_id, {**execution, 'pending': current}, version)
            return deepcopy(current)
        raise DomainError('Termination run is not in this attempt')

    @staticmethod
    def abandon_for_restart_in(uow, task, actor, validate_quiescent):
        """Explicit restart supersedes an uncertain attempt without replay/success."""
        execution, version = uow.execution.load(task.state.task_id)
        attempt = execution['pending']
        if not is_check_attempt(attempt):
            return None  # Other external protocols keep their existing recovery guards.
        validate_preserved_attempt_in(uow, task, actor, attempt)
        if validate_quiescent is None:
            raise DomainError('Task restart requires a check-runner recovery preflight')
        validate_quiescent(attempt)
        uow.execution.save(task.state.task_id, {**execution, 'pending': None}, version)
        return deepcopy(attempt)

    @staticmethod
    def finish_in(uow, task, actor, tree, execution_key, receipts):
        """Batch and pending removal share the caller's single Unit of Work."""
        execution, version = uow.execution.load(task.state.task_id)
        change = CheckAttempts.finish(task, actor, execution, uow.evidence.list_for(task.state.task_id),
                                      tree, execution_key, receipts)
        uow.tasks.save(change, task.state.version)
        uow.execution.save(task.state.task_id, {**execution, 'pending': None}, version)

    @staticmethod
    def finish(task, actor, execution, records, tree, execution_key, receipts):
        attempt = validate_attempt(task, actor, execution['pending'], tree, execution_key)
        recorded = check_receipts_by_id(records)
        if (len(receipts) != len(attempt['runs']) or any(
                not run['started'] or not receipt_matches(attempt, run, receipt)
                or recorded.get(run['run_id']) != receipt
                for run, receipt in zip(attempt['runs'], receipts, strict=True))):
            raise DomainError('Check attempt requires its exact immutable receipts')
        change = task.record_observations(actor, tree, execution_key, receipts)
        return change
