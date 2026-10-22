from __future__ import annotations
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
from ..modules.tasks.domain import (
    Task,
    TaskStageContracts,
    TaskState,
    TaskStatus,
)
from ..modules.workflow.domain import RouteDefinition
from ..modules.tasks.ports import RepositoryTreeReader, TaskUnitOfWork, TaskReadSet
from ..modules.tasks.contracts import stages_from_workflow, evidence_plan_from_metadata
from ..modules.verification.domain import CheckRegistry
from ..modules.content_requirements.domain import ArtifactFact, Assessment
from ..modules.foundation.errors import DomainError, VersionConflict
from ..modules.tasks.newborn import NewbornTask
from ..modules.tasks.definition import build_task, validate_creation
from ..modules.tasks.replay import ReplayIntent, ReplayResult
from .check_attempts import CheckAttempts, is_check_attempt


def require_reviewer_identity_in(
    uow: TaskUnitOfWork, task: Task, actor: str, *,
    target_stage: str | None = None, acquiring: bool = False,
) -> dict | None:
    return require_reviewer_identity(uow.tasks, task, actor,
                                     target_stage=target_stage, acquiring=acquiring)


def require_reviewer_identity(
    source, task: Task, actor: str, *,
    target_stage: str | None = None, acquiring: bool = False,
) -> dict | None:
    """Require distinct effective actors only for different configured stage roles."""
    from ..modules.workflow.domain import HandlerKind
    current = task.stage.stage_id
    node = task.route.node(current)
    if (target_stage is None and node.handler == HandlerKind.INSPECT
            and task.state.status in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED)
            and task.progress.outcome is not None):
        following = node.target(task.progress.outcome)
        if following is None or task.route.node(following).handler != HandlerKind.INSPECT:
            return None  # Consuming a completed review is not performing that review.
    target = current if target_stage is None else target_stage
    if (acquiring and task.state.claimed_by is None
            and task.state.status in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED)
            and task.progress.outcome is not None):
        target = task.route.node(current).target(task.progress.outcome) or current
    if task.route.node(target).handler != HandlerKind.INSPECT:
        return None
    identity = source.review_identity(task.state.task_id)
    if identity is None:
        return None  # An inspection route may begin with externally supplied input.
    producer_role = task.route.node(identity["stage"]).role
    inspection_role = task.route.node(target).role
    if producer_role == inspection_role:
        return None  # A single-role workflow does not require identity separation.
    executor = identity["executor_actor"]
    if executor is None:
        raise DomainError(
            "Reviewer identity provenance is unavailable for the produced result; "
            "a distinct native reviewer session must be established from saved evidence"
        )
    if executor == actor:
        raise DomainError(
            f"Reviewer actor {actor!r} equals executor {executor!r} "
            f"({identity['source']}, stage {identity['stage']}); "
            "use a distinct native reviewer session and public handoff"
        )
    return {**identity, "reviewer_actor": actor, "distinct_actor": True}


def _action_digest(action: str, payload: dict) -> str:
    value = {"action": action, **deepcopy(payload)}
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _creation_values(
    intent, allocation, actor, workflow, automatic_checks, decomposition_policy,
    base_metadata, requirements_context,
):
    from ..modules.tasks.allocation import materialize_contract
    from ..modules.tasks.definition import build_task, validate_creation
    contract, creation_request = materialize_contract(intent, allocation.task_id)
    if creation_request is not None:
        creation_request = {
            "request_id": allocation.request_id,
            "digest": allocation.digest,
        }
    metadata = validate_creation(
        contract, workflow, automatic_checks, decomposition_policy
    )
    metadata.update(base_metadata)
    metadata.update(sprint_id=contract['sprint_id'], goal=contract['goal'])
    if creation_request is not None:
        metadata['creation_request'] = creation_request
    task = build_task(metadata, actor)
    from ..modules.tasks.stage_criteria import StageCriteriaBook
    book = StageCriteriaBook.initialize_initial(task.route, task.stage_contracts,
        contract, requirements_context['snapshot'])
    task = replace(task, criteria_book=book)
    metadata['stage_criteria'] = book.data
    return task, metadata, contract


@dataclass(frozen=True)
class PreparedCreation:
    source_intent: dict
    intent: dict
    workflow: dict
    automatic_checks: list
    decomposition_policy: dict
    requirements_context: dict | None
    requirements_gate: object


def create_planned_in_uow(uow, prepared, base_metadata, policy, reserved_ids=()):
    from ..modules.tasks.allocation import TaskIdPolicy, creation_parts
    if not isinstance(prepared, PreparedCreation):
        raise DomainError("Task creation requires a verified creation preflight")
    source_intent = deepcopy(prepared.source_intent)
    intent = deepcopy(prepared.intent)
    workflow = deepcopy(prepared.workflow)
    automatic_checks = deepcopy(prepared.automatic_checks)
    decomposition_policy = deepcopy(prepared.decomposition_policy)
    request_id, _, _ = creation_parts(source_intent)
    parsed_policy = None if request_id is None else TaskIdPolicy.parse(policy)
    allocation = uow.tasks.allocate(source_intent, parsed_policy, reserved_ids)
    task, metadata, contract = _creation_values(
        intent, allocation, None, workflow, automatic_checks, decomposition_policy,
        base_metadata, prepared.requirements_context
    )
    if not allocation.replayed:
        uow.tasks.create(task, metadata)
    prepared.requirements_gate.publish_created(
        uow, allocation.task_id, prepared.requirements_context
    )
    return allocation, contract


def materialize_draft_task_identity_in(uow: TaskUnitOfWork, intent, policy, reserved_ids=()):
    """Resolve draft identity through the Task owner in the caller's transaction.

    A draft may be incomplete; this does not validate or publish its execution
    contract. Sprint membership and graph updates remain owned by Sprint.
    """
    from ..modules.tasks.allocation import TaskIdPolicy, materialize_contract

    if isinstance(intent, dict) and set(intent) == {"request_id", "task"}:
        allocation = uow.tasks.allocate(intent, TaskIdPolicy.parse(policy), reserved_ids)
        contract, creation_request = materialize_contract(intent, allocation.task_id)
        return contract, creation_request, allocation.request_id
    contract = deepcopy(intent)
    alias = contract.get("id") if isinstance(contract, dict) else None
    return contract, None, alias


def creation_batch_reservations(intents, additional=()):
    from ..modules.tasks.allocation import creation_parts
    reserved = set(additional)
    for intent in intents:
        request_id, task, _ = creation_parts(intent)
        if request_id is None:
            reserved.add(task['id'])
    return frozenset(reserved)


def rewrite_task_plan(plan, contracts_by_alias):
    data = dict(plan)
    data['tasks'] = [contracts_by_alias[key] for key in contracts_by_alias]
    aliases = {alias: contract['id'] for alias, contract in contracts_by_alias.items()}
    data['dependencies'] = [
        {**edge,
         'predecessor':aliases[edge['predecessor']],
         'successor':aliases[edge['successor']]}
        for edge in plan['dependencies']
    ]
    return data


def validate_creation_intent(intent, workflow, automatic_checks, decomposition_policy):
    from ..modules.tasks.allocation import creation_alias, materialize_contract
    from ..modules.tasks.definition import validate_creation
    contract, _ = materialize_contract(intent, creation_alias(intent))
    return validate_creation(contract, workflow, automatic_checks, decomposition_policy)


def creation_intent_alias(intent):
    from ..modules.tasks.allocation import creation_alias
    return creation_alias(intent)


@dataclass(frozen=True)
class SubmissionReceipt:
    submission_id: int
    created: bool
    version: int
    digest: str
    registry_change: dict | None = None


def require_start_execution(execution):
    """One execution prerequisite for start and read-only discovery."""
    if execution is not None:
        if execution['pending'] is not None:
            raise DomainError('Restarted Task execution recovery is still pending')


class TaskCommands:
    """One application API for Task changes. Every call uses a short UoW."""
    def __init__(self, unit_of_work: Callable[[], TaskUnitOfWork], repository_tree: RepositoryTreeReader,
                 requirements_gate):
        self.unit_of_work = unit_of_work
        self.repository_tree = repository_tree
        self.requirements_gate = requirements_gate

    def execution_preflight(self, task_id):
        from .sprints import require_sprint_execution
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,), related_sprints=True))
        require_sprint_execution(inputs, task_id)

    def reviewer_preflight(self, task_id: str, actor: str, *, acquiring: bool = False) -> dict | None:
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,), review_ids=(task_id,)))
        if inputs.state(task_id).status == TaskStatus.NEWBORN:
            return None
        return require_reviewer_identity(inputs, inputs.task(task_id), actor, acquiring=acquiring)

    def require_bootstrap_contract(self, task_id: str, contract: dict) -> None:
        """Accept only an exact saved canonical or full approved contract."""
        saved = self.unit_of_work().capture(TaskReadSet((task_id,))).restart_context(task_id)
        if contract == saved['contract']:
            return  # Preserve the existing public canonical-contract continuation.
        candidate, _ = self.requirements_gate.prepare_restarted_contract(
            contract, saved['requirements_snapshot'], saved['requirements_agreement']
        )
        if candidate != saved['contract']:
            raise DomainError('Existing task contract is immutable; bootstrap is not an editor')

    def prepare_creation(self, intent, workflow, automatic_checks, base_revision, decomposition_policy, *, current_registry=None):
        return self._prepare_creation(
            intent,
            workflow,
            automatic_checks,
            base_revision,
            decomposition_policy,
            self.requirements_gate.prepare_contract,
            current_registry=current_registry,
        )

    def _prepare_restarted_creation(
        self, intent, workflow, automatic_checks, base_revision, decomposition_policy, persisted_context,
        *, current_registry=None
    ):
        return self._prepare_creation(
            intent,
            workflow,
            automatic_checks,
            base_revision,
            decomposition_policy,
            lambda candidate: self.requirements_gate.prepare_restarted_contract(
                candidate,
                persisted_context["requirements_snapshot"],
                persisted_context["requirements_agreement"],
            ),
            current_registry=current_registry,
        )

    def _prepare_creation(
        self, intent, workflow, automatic_checks, base_revision, decomposition_policy, prepare_requirements,
        *, current_registry=None
    ):
        from ..modules.tasks.allocation import creation_alias, materialize_contract
        from ..modules.tasks.creation_preflight import CreationPreflight
        from ..modules.tasks.definition import validate_creation
        source_intent = deepcopy(intent)
        intent, requirements_context = prepare_requirements(intent)
        candidate, _ = materialize_contract(intent, creation_alias(intent))
        try:
            preflight = CreationPreflight.parse(candidate, workflow)
        except KeyError as exc:
            validate_creation(
                candidate, workflow, automatic_checks, decomposition_policy, current_registry=current_registry
            )
            raise DomainError(
                f"Task creation preflight requires field {exc.args[0]!r}"
            ) from exc
        if not isinstance(base_revision, str) or not base_revision:
            raise DomainError("Task creation requires an explicit repository tree preflight")
        preflight.validate_base(
            self.repository_tree.existing_paths(base_revision, preflight.repository_inputs)
        )
        validate_creation(candidate, workflow, automatic_checks, decomposition_policy,
                          current_registry=current_registry)
        return PreparedCreation(
            source_intent,
            deepcopy(intent),
            deepcopy(workflow),
            deepcopy(automatic_checks),
            deepcopy(decomposition_policy),
            deepcopy(requirements_context),
            self.requirements_gate,
        )

    def publish_requirements(self, task_id, task_requirements, agreement):
        return self.requirements_gate.publish(task_id, task_requirements, agreement)

    def requirements_snapshot(self, task_id):
        return self.requirements_gate.snapshot(task_id)

    def preview_creation_allocation(self, intent: dict, policy):
        """Choose an identity from a closed input view; never reserve it."""
        from ..modules.tasks.allocation import TaskIdPolicy, creation_parts
        request_id, _, _ = creation_parts(intent)
        parsed = None if request_id is None else TaskIdPolicy.parse(policy)
        inputs = self.unit_of_work().capture(TaskReadSet((), allocation=True,
            creation_requests=() if request_id is None else (request_id,)))
        return inputs.allocate(intent, parsed)

    def create(self, intent: dict, actor: str, workflow: dict, automatic_checks: list,
               base_metadata: dict, execution, policy, base_revision,
               decomposition_policy):
        from .ownership import prepare_actor_release
        from .task_preparation import compile_creation
        from ..modules.tasks.allocation import TaskIdPolicy, creation_parts
        request_id, _, _ = creation_parts(intent)
        parsed = None if request_id is None else TaskIdPolicy.parse(policy)
        requests = () if request_id is None else (request_id,)
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((), actors=(actor,), allocation=True,
                                            creation_requests=requests))
        allocation = inputs.allocate(intent, parsed)
        if allocation.replayed:
            return allocation
        prepared = self.prepare_creation(intent, workflow, automatic_checks, base_revision,
                                         decomposition_policy)
        release = prepare_actor_release(facade, inputs, actor, None)
        namespace = inputs
        while True:
            allocation = namespace.allocate(intent, parsed)
            if allocation.replayed:
                return allocation
            write = compile_creation(facade, prepared, allocation, base_metadata, None, actor=actor)
            with self.unit_of_work() as uow:
                replay = uow.tasks.creation_replay(allocation)
                ready = replay is None and uow.tasks.require_creation_allocation(allocation, None)
                if ready:
                    uow.require_inputs(inputs)
                    if release is not None:
                        release.save(uow)
                    write.save(uow)
            if replay is not None:
                return replay
            if ready:
                return allocation
            namespace = facade.capture(TaskReadSet((), allocation=True, creation_requests=requests))

    def create_duplicate(self, task_id, parent_id, sprint_id, actor, config_hash, request_id):
        from .duplicate_tasks import duplicate_draft
        from ..modules.tasks.duplicates import family_projection
        identity = _action_digest('duplicate', {
            'task_id':task_id,'parent_id':parent_id,'sprint_id':sprint_id})
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        if sprint_id is None:
            raise DomainError('Duplicate requires a destination Sprint')
        inputs = facade.capture(TaskReadSet((parent_id,), optional_ids=(task_id,),
            family_contract_ids=(parent_id,), sprint_ids=(sprint_id,)))
        family = inputs.read_duplicate_family(parent_id)
        source_id = parent_id
        parent_id = source_id if family is None else family['parent_id']
        if task_id == parent_id or inputs.exists(task_id):
            raise DomainError('Duplicate requires a new Task identity')
        context = inputs.restart_context(parent_id)
        if not isinstance(context['contract'], dict) or not isinstance(context['workflow'], dict):
            raise DomainError('Duplicate requires an established parent contract')
        record = inputs.sprint(sprint_id)
        if record is None:
            raise DomainError('Unknown Sprint for duplicate membership')
        newborn, sprint = duplicate_draft(task_id, sprint_id, context, record)
        metadata, event = facade.prepare_duplicate_newborn(newborn, config_hash, parent_id)
        sprint_write = facade.prepare_sprint({**record, 'actor':actor, 'aggregate':sprint.to_dict()})
        members = ([{'task_id':source_id}] if family is None else family['members']) + [{'task_id':task_id}]
        result = newborn.describe() | {'sprint_revision':sprint.revision,
            'duplicate':family_projection({'parent_id':parent_id, 'members':members}, task_id)}
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                uow.require_inputs(inputs)
                uow.tasks.create_newborn_prepared(newborn, metadata)
                uow.tasks.record_duplicate_link(task_id, actor, event)
                uow.sprints.save_prepared(sprint_write, sprint.revision - 1)
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    def create_newborn(self, task_id, sprint_id, actor, config_hash, request_id, *,
                       goal_type=None, patch=None, workflows=None, planning_source=None):
        from ..application.ownership import prepare_actor_release
        from ..modules.sprints.domain import Sprint
        intent = {"task_id": task_id, "sprint_id": sprint_id}
        if goal_type is not None or patch is not None:
            intent.update(goal_type=goal_type, patch=deepcopy(patch))
        identity = _action_digest("create", intent)
        newborn = NewbornTask.create(task_id, sprint_id, actor)
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((), actors=(actor,), optional_ids=(task_id,),
                                           sprint_ids=() if sprint_id is None else (sprint_id,)))
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        if goal_type is not None or patch is not None:
            if patch is not None and not isinstance(patch, dict):
                raise DomainError('Task creation patch must be an object')
            changes = deepcopy(patch) if patch is not None else {}
            mapping = deepcopy(workflows) if workflows is not None else {}
            if goal_type is not None:
                if not isinstance(goal_type, str) or not goal_type.strip():
                    raise DomainError('Task goal_type must be a nonempty string')
                if 'goal_type' in changes and changes['goal_type'] != goal_type:
                    raise DomainError('Task goal_type selection conflicts with creation patch')
                changes['goal_type'] = goal_type
                if 'workflow' not in changes:
                    if planning_source is None:
                        raise DomainError('Task starter source is not configured')
                    defaults, workflow = planning_source(goal_type, task_id, sprint_id)
                    changes = {**defaults, **changes}
                    mapping[goal_type] = workflow
            newborn = newborn.edit(changes, [], mapping, actor)
        release = prepare_actor_release(facade, inputs, actor, task_id)
        sprint_write = None
        sprint_revision = None
        if sprint_id is not None:
            record = inputs.sprint(sprint_id)
            if record is None:
                raise DomainError('Unknown Sprint for newborn membership')
            sprint = Sprint.restore(record['aggregate']).add_newborn_member(task_id)
            record = {**record, 'actor': actor, 'aggregate': sprint.to_dict()}
            sprint_write = facade.prepare_sprint(record)
            sprint_revision = sprint.revision
        metadata = facade.prepare_newborn_create(newborn, config_hash)
        result = newborn.describe() | ({'sprint_revision': sprint_revision} if sprint_id is not None else {})
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                uow.require_inputs(inputs)
                if release is not None:
                    release.save(uow)
                uow.tasks.create_newborn_prepared(newborn, metadata)
                if sprint_write is not None:
                    uow.sprints.save_prepared(sprint_write, sprint_revision - 1)
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    def recover_cancelled(
        self, task_id, actor, expected_version, request_id, reason, authorization,
        validate_execution: Callable[[dict], None],
    ):
        from ..modules.task_cleanup.domain import CleanupRun
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("Cancellation recovery request_id is required")
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError("Cancellation recovery expected_version must be nonnegative")
        if not isinstance(authorization, str) or not authorization.strip():
            raise DomainError("Cancellation recovery authorization is required")
        identity = _action_digest("recover_cancelled", {
            "task_id": task_id, "actor": actor, "expected_version": expected_version,
            "reason": reason, "authorization": authorization,
        })
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,),
            actors=(actor,), history_ids=(task_id,)))
        with self.unit_of_work() as uow:
            observed_ownership = uow.ownership.preflight(actor, task_id)
        task = inputs.task(task_id)
        if task.state.version != expected_version:
            raise VersionConflict("Cancellation recovery version changed")
        if task.state.status != TaskStatus.CANCELLED:
            raise DomainError("Recovery requires a cancelled unfinished Task")
        ownership = observed_ownership
        if ownership.task_owner not in (None, actor) or ownership.worktree_owner not in (None, actor):
            raise DomainError("Recovery target is owned by another session")
        before = inputs.ownership(actor)
        if before.task_id not in (None, task_id):
            raise DomainError("Recovery requires an idle or owning session")
        previous = inputs.cancellation_recovery_point(task_id, expected_version)
        change = task.recover_cancelled(actor, previous, reason, authorization)
        execution = None
        pending = None
        if inputs.execution(task_id) is not None:
            execution, execution_version = inputs.required_execution(task_id)
            if execution["publication"] is not None:
                raise DomainError("Resolve publication/integration before cancellation recovery")
            pending = execution["pending"]
            if pending is not None:
                if not isinstance(pending, dict) or pending.get("kind") != "task_cleanup":
                    raise DomainError("Resolve pending external outcome before recovery")
                cleanup = CleanupRun.restore(pending)
                if (cleanup.intent.task_id != task_id
                        or cleanup.intent.request_id != f"cancel-{previous.version}"
                        or cleanup.removed or cleanup.version > 1
                        or (cleanup.disposition is not None
                            and cleanup.disposition.kind != "no_resources")):
                    raise DomainError("Resolve pending cleanup/disposition before recovery")
            validate_execution(execution)
        result = {"status": "task_recovered", "task": task_id,
                  "task_status": change.task.state.status.value,
                  "version": change.task.state.version, "request_id": request_id,
                  "authorization": authorization, "reason": reason,
                  "withdrawn_cleanup": deepcopy(pending), "replayed": False}
        write = facade.prepare_change(inputs, change)
        execution_write = (None if pending is None else
            facade.prepare_execution(inputs, task_id, {**execution, 'pending':None}))
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != observed_ownership:
                    raise VersionConflict('Cancellation recovery ownership changed; repeat the request')
                uow.tasks.save_prepared(write)
                if execution_write is not None:
                    uow.execution.save_prepared(task_id, execution_write, execution_version)
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    def restart_newborn(
        self, task_id, actor, expected_version, request_id, reason, authorization,
        *, validate_reuse_restart=None, validate_check_restart=None,
    ):
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("Task restart request_id is required")
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError("Task restart expected_version must be a nonnegative integer")
        identity = _action_digest("restart", {
            "task_id": task_id,
            "expected_version": expected_version,
            "reason": reason,
            "authorization": authorization,
        })
        from .ownership import prepare_actor_release
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), actors=(actor,),
            review_unsubmitted_ids=(task_id,), family_execution_ids=(task_id,), history_ids=(task_id,)))
        with self.unit_of_work() as uow:
            ownership = uow.ownership.preflight(actor, task_id)
        if inputs.state(task_id).status == 'newborn':
            current = inputs.newborn(task_id)
            if current.version != expected_version:
                raise VersionConflict("Task restart version changed")
            target = ownership
            if target.worktree_owner not in (None, actor):
                raise DomainError('Task workspace is owned by another session; handoff is required')
            if (isinstance(authorization, dict) and authorization.get('role') == 'reviewer'
                    and (current.draft.get('planning') is not None or current.restart_history)):
                reviewer = inputs.review_identity(task_id, include_unsubmitted=True)
                if reviewer is not None and reviewer['executor_actor'] in (None, actor):
                    raise DomainError('Task restart requires a distinct reviewer or user decision')
            changed = current.restart_draft(actor, reason, authorization)
            before = inputs.ownership(actor)
            context = inputs.restart_context(task_id)
            newborn = changed
            drafted = True
        else:
            task = inputs.task(task_id)
            if task.state.version != expected_version:
                raise VersionConflict("Task restart version changed")
            target = ownership
            if target.task_owner not in (None, actor):
                raise DomainError("Task is owned by another session; handoff is required")
            if target.worktree_owner not in (None, actor):
                raise DomainError("Task worktree is owned by another session; handoff is required")
            context = inputs.restart_context(task_id)
            if (context['contract'].get('planning') is not None
                    and isinstance(authorization, dict)
                    and authorization.get('role') == 'reviewer'):
                reviewer = inputs.review_identity(task_id, include_unsubmitted=True)
                if reviewer is not None and reviewer['executor_actor'] in (None, actor):
                    raise DomainError('Task restart requires a distinct reviewer or user decision')
            restart_contract = deepcopy(context["contract"])
            if context["requirements_snapshot"] is not None:
                restart_contract["requirements_snapshot"] = deepcopy(
                    context["requirements_snapshot"]
                )
                restart_contract["requirements_agreement"] = deepcopy(
                    context["requirements_agreement"]
                )
            from .duplicate_tasks import restart_local_repair
            recovery = {}
            local_repair = restart_local_repair(
                inputs, inputs, task, context['restart_history'], validate_reuse_restart)
            if local_repair is not None:
                recovery['local_repair'] = local_repair
            if inputs.execution(task_id) is not None:
                from .check_attempts import validate_preserved_attempt
                attempt = inputs.execution(task_id)[0]['pending']
                if is_check_attempt(attempt):
                    validate_preserved_attempt(inputs, task, actor, attempt)
                    if validate_check_restart is None:
                        raise DomainError('Task restart requires a check-runner recovery preflight')
                    validate_check_restart(attempt)
                    recovery['abandoned_check_attempt'] = deepcopy(attempt)
                elif attempt is not None:
                    raise DomainError('Task has an unknown external outcome; complete its pending recovery protocol before restart')
            newborn = NewbornTask.restart(
                task,
                restart_contract,
                context["workflow"],
                context["sprint_id"],
                actor,
                reason,
                authorization,
                context["restart_history"],
                context["creation_request"],
                context["stage_contract_history"],
                recovery=recovery,
            )
            drafted = False
        release = prepare_actor_release(facade, inputs, actor, task_id)
        metadata = facade.prepare_newborn(inputs, newborn, context['config_hash'])
        result = {**newborn.describe(), 'replayed':False}
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        event = facade.prepare_restart_event(reason, authorization)
        execution = inputs.execution(task_id)
        execution_write = None
        if not drafted and execution is not None:
            execution_write = facade.prepare_execution(inputs, task_id,
                {**execution[0], 'attempts':0, 'publication':None, 'pending':None,
                 'last_report':None})
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != ownership:
                    raise VersionConflict('Restart ownership changed; repeat the request')
                if release is not None:
                    release.save(uow)
                if drafted:
                    uow.tasks.save_newborn_prepared(newborn, expected_version, metadata, 'restarted_newborn')
                else:
                    uow.work_packets.invalidate(task_id)
                    if execution_write is not None:
                        uow.execution.save_prepared(task_id, execution_write, execution[1])
                    uow.tasks.restart_newborn_prepared(newborn, expected_version, metadata, event)
                    if execution is not None and context['workflow']['worktree_required']:
                        uow.ownership.bind_worktree(actor, task_id)
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    @staticmethod
    def _stage_contract_authorization(value, required_role):
        if value != {"role": required_role}:
            raise DomainError(f"stage contract action requires {required_role} authorization")

    def initialize_stage_contracts(
        self, task_id, actor, expected_version, request_id, contracts, reason, authorization
    ):
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("stage contract request_id is required")
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError("stage contract expected_version must be a nonnegative integer")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("stage contract transition reason is required")
        new = deepcopy(contracts)
        audit = {
            "action": "initialize_stage_contracts",
            "actor": actor,
            "authorization": deepcopy(authorization),
            "expected_version": expected_version,
            "new": new,
            "old": None,
            "reason": reason,
            "request_id": request_id,
        }
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            replay = uow.tasks.stage_contract_receipt_raw(task_id, request_id)
        if replay is not None:
            return facade.restore_stage_contract_receipt(replay, task_id, request_id, audit)
        inputs = facade.capture(TaskReadSet((task_id,)))
        self._stage_contract_authorization(authorization, "creator")
        task = inputs.task(task_id)
        if task.state.version != expected_version:
            raise DomainError("stage contract version conflict")
        parsed = TaskStageContracts.parse(contracts, task.route, task.content_policy)
        from ..modules.tasks.creation_preflight import CreationPreflight
        context = inputs.restart_context(task_id)
        CreationPreflight.parse(
            {**context["contract"], "stage_contracts": [item.to_dict() for item in parsed.items]},
            context["workflow"],
        )
        change = task.initialize_stage_contracts(parsed)
        prepared = facade.prepare_stage_contract_change(inputs, change, audit)
        with self.unit_of_work() as uow:
            replay = uow.tasks.stage_contract_receipt_raw(task_id, request_id)
            if replay is None:
                uow.require_inputs(inputs)
                uow.tasks.save_prepared_stage_contract_change(prepared)
        return prepared.result if replay is None else facade.restore_stage_contract_receipt(replay, task_id, request_id, audit)

    def revise_stage_contract(
        self, task_id, actor, expected_version, request_id, stage_id, contract,
        reason, authorization
    ):
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("stage contract request_id is required")
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError("stage contract expected_version must be a nonnegative integer")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("stage contract revision reason is required")
        from .ownership import prepare_actor_release
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), actors=(actor,)))
        task = inputs.task(task_id)
        context = inputs.restart_context(task_id)
        if context['contract'].get('planning') is not None:
            raise DomainError('Frozen Task planning contract requires authorized restart')
        old = task._require_stage_contracts().stage(stage_id).to_dict()
        audit = {
            "action": "revise_stage_contract",
            "actor": actor,
            "authorization": deepcopy(authorization),
            "expected_version": expected_version,
            "new": deepcopy(contract),
            "old": old,
            "reason": reason,
            "request_id": request_id,
        }
        with self.unit_of_work() as uow:
            replay = uow.tasks.stage_contract_receipt_raw(task_id, request_id)
        if replay is not None:
            return facade.restore_stage_contract_receipt(replay, task_id, request_id, audit)
        self._stage_contract_authorization(authorization, "reviewer")
        if task.state.version != expected_version:
            raise DomainError("stage contract version conflict")
        with self.unit_of_work() as uow:
            target = uow.ownership.preflight(actor, task_id)
        if target.task_owner not in (None, actor):
            raise DomainError("Stage contract Task is owned by another session; handoff is required")
        if target.worktree_owner not in (None, actor):
            raise DomainError("Stage contract worktree is owned by another session; handoff is required")
        if not isinstance(contract, dict) or set(contract) != {
            "stage_id", "allowed_paths", "entry_requirements", "exit_requirements", "criteria"
        } or contract["stage_id"] != stage_id:
            raise DomainError("Exact replacement stage contract is required")
        candidate = [
            deepcopy(contract) if item.stage_id == stage_id else item.to_dict()
            for item in task.stage_contracts.items
        ]
        parsed = TaskStageContracts.parse(candidate, task.route, task.content_policy)
        from ..modules.tasks.creation_preflight import CreationPreflight
        CreationPreflight.parse(
            {**context["contract"], "stage_contracts": [item.to_dict() for item in parsed.items]},
            context["workflow"],
        )
        release = prepare_actor_release(facade, inputs, actor, task_id)
        replacement = parsed.stage(stage_id)
        change = task.revise_stage_contract(actor, stage_id, replacement)
        prepared = facade.prepare_stage_contract_change(inputs, change, audit)
        required = inputs.worktree_required(task_id)
        with self.unit_of_work() as uow:
            replay = uow.tasks.stage_contract_receipt_raw(task_id, request_id)
            if replay is None:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != target:
                    raise VersionConflict('Stage contract ownership changed; repeat the request')
                if release is not None:
                    release.save(uow)
                uow.tasks.save_prepared_stage_contract_change(prepared)
                if required:
                    uow.ownership.bind_worktree(actor, task_id)
        return prepared.result if replay is None else facade.restore_stage_contract_receipt(replay, task_id, request_id, audit)

    def stage_contract_context(self, task_id):
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        inputs.task(task_id)._require_stage_contracts()
        context = inputs.restart_context(task_id)
        return {'current':deepcopy(context['contract'].get('stage_contracts')),
                'history':deepcopy(context['stage_contract_history'])}

    def edit_newborn(self, task_id, actor, expected_revision, patch, remove, workflows,
                     config_hash, request_id, *, planning_source=None):
        from ..application.ownership import prepare_actor_release
        if type(expected_revision) is not int:
            raise DomainError('Newborn edit requires expected_revision')
        identity = _action_digest("edit", {
            "task_id": task_id,
            "expected_revision": expected_revision,
            "patch": patch,
            "remove": remove,
        })
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), actors=(actor,)))
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        newborn = inputs.newborn(task_id)
        if newborn.version != expected_revision:
            raise VersionConflict('Newborn Task revision changed')
        if newborn.claimed_by not in (None, actor):
            raise DomainError('Newborn Task is owned by another session')
        with self.unit_of_work() as uow:
            uow.ownership.preflight(actor, task_id)
        release = prepare_actor_release(facade, inputs, actor, task_id)
        effective_patch = deepcopy(patch)
        effective_workflows = workflows
        if (planning_source is not None and isinstance(patch, dict)
                and 'goal_type' in patch and 'workflow' not in patch
                and (newborn.workflow is None or patch['goal_type'] != newborn.draft.get('goal_type'))):
            defaults, workflow = planning_source(patch['goal_type'], task_id, newborn.sprint_id)
            effective_patch = {**{key: value for key, value in defaults.items()
                                 if key not in newborn.draft}, **patch}
            effective_workflows = {**workflows, patch['goal_type']: workflow}
        changed = newborn.edit(effective_patch, remove, effective_workflows, actor)
        if (newborn.workflow is not None and changed.workflow is not None
                and newborn.workflow['worktree_required'] != changed.workflow['worktree_required']
                and inputs.execution(task_id) is not None):
            execution, _ = inputs.required_execution(task_id)
            if (not changed.workflow['worktree_required']
                    or any(execution[key] is not None for key in
                           ('worktree', 'branch', 'pending', 'publication', 'last_report'))):
                raise DomainError(
                    'Restart preserves the allocated workspace; changing worktree_required '
                    'requires resolving that resource through its owner, not a draft edit')
        metadata = facade.prepare_newborn(inputs, changed, config_hash)
        result = changed.describe()
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                uow.require_inputs(inputs)
                uow.ownership.preflight(actor, task_id)
                if release is not None:
                    release.save(uow)
                uow.tasks.save_newborn_prepared(changed, newborn.version, metadata, 'newborn_edited')
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    def prepare_newborn_ready(self, task_id, actor, automatic_checks,
                              decomposition_policy, config_hash, request_id, creation_base):
        """Prepare one Task-owned candidate for ready and Sprint republication."""
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,),
                                           actors=(actor,), related_sprints=True))
        newborn = inputs.newborn(task_id)
        if newborn.claimed_by not in (None, actor):
            raise DomainError('Newborn Sprint member is owned by another session')
        if newborn.workflow is None:
            raise DomainError('Select goal_type before ready')
        contract = {'id':task_id, 'sprint_id':newborn.sprint_id, **deepcopy(newborn.draft)}
        snapshot = newborn
        context = inputs.restart_context(task_id)
        sprint_snapshot = None if newborn.sprint_id is None else inputs.sprint(newborn.sprint_id)
        execution_snapshot = inputs.execution(task_id) if inputs.execution(task_id) is not None else None
        executed_restart = any(item.get('from_status') != 'newborn' for item in newborn.restart_history)
        effective_hash = context['config_hash'] if executed_restart else config_hash
        effective_checks = automatic_checks
        effective_decomposition = decomposition_policy
        current_registry = None
        previous_registry = None
        registry_change = None
        if executed_restart:
            histories = newborn.restart_history
            start = max(index for index, item in enumerate(histories)
                        if item.get('from_status') != 'newborn')
            edited = frozenset(field for item in histories[start:]
                               for field in item.get('registry_edits', []))
            authority = histories[-1].get('planning_revision')
            from ..modules.tasks.domain import Task
            previous_registry = inputs.restarted_registry(task_id)
            current_registry, registry_change = Task.prepare_restarted_registry(
                previous_registry, contract, newborn.workflow, edited,
                request_id, None if authority is None else authority['allowed_changes'],
            )
        restart_base = None
        if newborn.restart_history and inputs.execution(task_id) is not None:
            restart_base = inputs.execution(task_id)[0]['base']
        if newborn.sprint_id is not None and executed_restart:
            sprint = inputs.sprint(newborn.sprint_id)
            if sprint is None or sprint['aggregate']['state'] not in ('draft', 'published'):
                raise DomainError('Restarted Sprint Task requires its published Sprint')
            effective_checks = sprint['automatic_checks']
            effective_decomposition = sprint['task_decomposition']
        base_revision = restart_base if restart_base is not None else creation_base()
        from ..modules.tasks.planning import require_revision
        resolved_revision = require_revision(contract, snapshot.workflow, snapshot.restart_history)
        frozen_context_unchanged = (
            contract.get('requirements_snapshot') == context['requirements_snapshot']
            and contract.get('requirements_agreement') == context['requirements_agreement']
        )
        if executed_restart and frozen_context_unchanged:
            prepared = self._prepare_restarted_creation(
                contract,
                snapshot.workflow,
                effective_checks,
                base_revision,
                effective_decomposition,
                context,
                current_registry=current_registry,
            )
        else:
            prepared = self.prepare_creation(
                contract,
                snapshot.workflow,
                effective_checks,
                base_revision,
                effective_decomposition,
                current_registry=current_registry,
            )
        metadata = validate_creation(
            prepared.intent, newborn.workflow, effective_checks,
            effective_decomposition,
            current_registry=current_registry,
        )
        metadata.update(sprint_id=newborn.sprint_id, goal=contract['goal'], config_hash=effective_hash)
        if newborn.creation_request is not None:
            metadata['creation_request'] = deepcopy(newborn.creation_request)
        if newborn.restart_history:
            metadata['restart_history'] = list(deepcopy(newborn.restart_history))
            if executed_restart:
                metadata['restart_history'][-1]['before_requirements_snapshot'] = deepcopy(
                    context['requirements_snapshot']
                )
                metadata['restart_history'][-1]['before_requirements_agreement'] = deepcopy(
                    context['requirements_agreement']
                )
            if resolved_revision is not None:
                metadata['restart_history'][-1]['resolved_revision'] = resolved_revision
        if newborn.stage_contract_history:
            metadata['stage_contract_history'] = list(
                deepcopy(newborn.stage_contract_history)
            )
        candidate = {'snapshot': snapshot, 'creation': prepared, 'metadata': metadata,
                'registry': current_registry, 'previous_registry': previous_registry,
                'registry_change': registry_change,
                'executed_restart': executed_restart, 'restart_base': restart_base,
                'frozen_context_unchanged': frozen_context_unchanged,
                'criteria_book': context['stage_criteria'],
                'resolved_revision': resolved_revision, 'config_hash': effective_hash,
                'sprint_snapshot': sprint_snapshot, 'execution_snapshot': execution_snapshot, 'inputs': inputs}

        self._prepare_newborn_promotion(facade, candidate, actor)
        return candidate

    def _prepare_newborn_promotion(self, facade, candidate, actor):
        newborn = candidate['snapshot']
        task_id = newborn.task_id
        task = build_task(candidate['metadata'], None, current_registry=candidate['registry'])
        from ..modules.tasks.stage_criteria import StageCriteriaBook
        if candidate['criteria_book'] is not None:
            book = StageCriteriaBook.from_dict(candidate['criteria_book'])
            if candidate['executed_restart']:
                book = book.revise_after_restart(task.route, task.stage_contracts,
                    candidate['metadata']['restart_history'][-1], actor,
                    candidate['metadata']['contract'], candidate['creation'].requirements_context['snapshot'])
        else:
            if candidate['executed_restart']:
                raise DomainError('stage criteria migration required')
            book = StageCriteriaBook.initialize_initial(task.route, task.stage_contracts,
                candidate['metadata']['contract'], candidate['creation'].requirements_context['snapshot'])
        task = replace(task, criteria_book=book)
        candidate['metadata']['stage_criteria'] = book.data
        if candidate['registry_change'] is not None:
            task = task.bind_restarted_registry_audit(
                candidate['registry_change'], actor,
                candidate['previous_registry'].executable_obligations
            )
        if candidate['executed_restart']:
            task = replace(task, feedback=candidate['inputs'].restarted_feedback(task_id),
                           evidence_book=candidate['inputs'].restarted_evidence(task_id))
        metadata = self.requirements_gate.prepare_created_metadata(
            candidate['metadata'], candidate['creation'].requirements_context)
        source = facade.existing_promotion_source(candidate['inputs'], task_id)
        candidate['write'] = facade.prepare_promotion(source, task, metadata)
        candidate['registry_guard'] = (
            self.requirements_gate.prepare_current_guard(candidate['creation'].requirements_context)
            if candidate['executed_restart'] and not candidate['frozen_context_unchanged'] else None)
        candidate['release_worktree'] = bool(newborn.restart_history
            and candidate['restart_base'] is not None and candidate['inputs'].worktree_required(task_id)
            and candidate['inputs'].ownership(actor).worktree_task_id == task_id)

    @staticmethod
    def require_prepared_newborn_in(uow, candidate):
        newborn = candidate['snapshot']
        if uow.tasks.load_newborn(newborn.task_id) != newborn:
            raise VersionConflict('Newborn Task changed after creation preflight')
        if (newborn.sprint_id is not None
                and uow.sprints.get(newborn.sprint_id) != candidate['sprint_snapshot']):
            raise VersionConflict('Sprint changed after newborn creation preflight')
        execution = uow.execution.load(newborn.task_id) if uow.execution.exists(newborn.task_id) else None
        if execution != candidate['execution_snapshot']:
            raise VersionConflict('Newborn execution changed after creation preflight')

    def promote_newborn_ready_in(self, uow, candidate, actor):
        from ..modules.tasks.ports import NewbornPromotionBatch
        candidates = candidate.candidates if isinstance(candidate, NewbornPromotionBatch) else (candidate,)
        # All guards precede the first batch effect, including a shared actor's release.
        for item in candidates:
            uow.require_inputs(item['inputs'])
            if item['registry_guard'] is not None:
                item['registry_guard'].require()
        for item in candidates:
            if item['release_worktree']:
                uow.ownership.bind_worktree(actor, None)
            uow.tasks.promote_prepared(item['write'], item['snapshot'].version)

    def ready_newborn(self, task_id, actor, expected_revision, automatic_checks,
                      decomposition_policy, config_hash, request_id, creation_base):
        if type(expected_revision) is not int:
            raise DomainError('Newborn ready requires expected_revision')
        identity = _action_digest('ready', {
            'task_id': task_id, 'expected_revision': expected_revision,
        })
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
        if replay is not None:
            return replay.result()
        initial = facade.capture(TaskReadSet((task_id,)))
        newborn = initial.newborn(task_id)
        if newborn.version != expected_revision:
            raise VersionConflict('Newborn Task revision changed')
        if newborn.claimed_by != actor:
            raise DomainError('Newborn ready requires current ownership')
        candidate = self.prepare_newborn_ready(task_id, actor, automatic_checks,
            decomposition_policy, config_hash, request_id, creation_base)
        if candidate['snapshot'] != newborn:
            raise VersionConflict('Newborn Task changed after creation preflight')
        sprint = None if newborn.sprint_id is None else candidate['inputs'].sprint(newborn.sprint_id)
        if newborn.sprint_id is not None and sprint is None:
            raise DomainError('Newborn Sprint member has no Sprint')
        defer = sprint is not None and sprint['aggregate']['state'] == 'draft'
        if defer:
            ready = newborn.mark_ready()
            if candidate['resolved_revision'] is not None:
                ready = replace(ready, restart_history=tuple(candidate['metadata']['restart_history']))
            metadata = facade.prepare_newborn(candidate['inputs'], ready, candidate['config_hash'])
            result = ready.describe()
        else:
            result = {
                'status':'available', 'task':task_id, 'revision':newborn.version + 1,
                'sprint':newborn.sprint_id, 'claimed_by':None,
                'goal_type':newborn.draft['goal_type'],
                'route_entry':newborn.workflow['route']['entry'], 'ready':True,
            }
        receipt = facade.prepare_action_receipt(request_id, identity, result)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt_raw(task_id, request_id, identity)
            if replay is None:
                if defer:
                    uow.require_inputs(candidate['inputs'])
                    uow.tasks.save_newborn_prepared(ready, newborn.version, metadata, 'newborn_ready')
                else:
                    self.promote_newborn_ready_in(uow, candidate, actor)
                uow.tasks.remember_prepared_action(task_id, actor, receipt)
        return result if replay is None else replay.result()

    def read_duplicate_family(self, task_id):
        inputs = self.unit_of_work().capture(TaskReadSet((), family_ids=(task_id,)))
        return inputs.read_duplicate_family(task_id)

    def duplicate_preflight(self, task_id, actor, *, force_duplicate_start=False):
        from .duplicate_tasks import require_duplicate_start
        inputs = self.unit_of_work().capture(TaskReadSet((), family_ids=(task_id,)))
        return require_duplicate_start(inputs, task_id, actor,
            force_duplicate_start=force_duplicate_start)

    def start(self, task_id, actor, execution, *, force_duplicate_start=False,
              restart_entry_tree: Callable[[dict], str] | None = None):
        from .sprints import require_sprint_execution
        from .duplicate_tasks import require_duplicate_start
        from ..modules.tasks.ports import TaskSourcesChanged
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,),
            actors=(actor,), related_sprints=True, family_ids=(task_id,)))
        require_sprint_execution(inputs, task_id)
        task = inputs.task(task_id)
        duplicate = require_duplicate_start(inputs, task_id, actor,
            force_duplicate_start=force_duplicate_start)
        saved_execution = inputs.execution(task_id)
        has_execution = saved_execution is not None
        if not has_execution and duplicate is not None and duplicate['reason'] == 'local_repair':
            if self.repository_tree is None or not self.repository_tree.contains_commit(
                    execution['base'], duplicate['source']['source_commit']):
                raise DomainError('Local repair source is absent from reserved base; import it before start')
        change = task.start(actor)
        changes = (change,) if duplicate is not None else (change, change.task.release_ownership(actor))
        writes = facade.prepare_claim_changes(inputs, changes)
        execution_data = execution
        if has_execution:
            current, execution_version = saved_execution
            require_start_execution(current)
            before = inputs.ownership(actor)
            with self.unit_of_work() as uow:
                target = uow.ownership.preflight(actor, task_id)
            required = inputs.worktree_required(task_id)
            if before.task_id not in (None, task_id) or target.task_owner not in (None, actor):
                raise DomainError('Restart cannot replace other Task ownership')
            if required and (target.worktree_owner not in (None, actor)
                             or before.worktree_task_id not in (None, task_id)):
                raise DomainError('Restart cannot replace independent or foreign worktree ownership')
            if required and current['worktree'] is None:
                if any(current[key] is not None for key in
                       ('branch', 'pending', 'publication', 'last_report')):
                    raise DomainError('Resolve retained execution before workspace allocation')
                if execution['worktree'] is None or execution['pending']['kind'] != 'worktree_setup':
                    raise DomainError('Restart requires an explicit worktree reservation')
                execution_data = {**current, **execution}
            else:
                if restart_entry_tree is None:
                    raise DomainError('Restart start requires an observed retained workspace tree')
                entry_tree = restart_entry_tree(deepcopy(current))
                if (not isinstance(entry_tree, str) or len(entry_tree) != 40
                        or any(c not in '0123456789abcdef' for c in entry_tree)):
                    raise DomainError('Restart requires a valid Git entry tree')
                execution_data = {**current, 'entry_tree': entry_tree}
        execution_write = facade.prepare_execution(inputs, task_id, execution_data)
        try:
            with self.unit_of_work() as uow:
                uow.require_inputs(inputs)
                if has_execution:
                    fresh = uow.ownership.preflight(actor, task_id)
                    if fresh != target:
                        raise VersionConflict('Restart ownership changed; repeat the request')
                for write in writes:
                    uow.tasks.save_prepared(write)
                if has_execution:
                    uow.execution.save_prepared(task_id, execution_write, execution_version)
                else:
                    uow.execution.create_prepared(task_id, execution_write)
        except TaskSourcesChanged as conflict:
            require_duplicate_start(conflict.current, task_id, actor,
                force_duplicate_start=force_duplicate_start)
            raise

    def submit(self, task_id: str, actor: str, payload: dict) -> SubmissionReceipt:
        if not isinstance(payload, dict) or set(payload) != {"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Требуется точный stage result: sections, artifact_paths, commit_message, content_additions, trace, method_additions, stage_work, evidence_work")
        if not isinstance(payload["artifact_paths"],list):
            raise DomainError("artifact_paths должен быть списком путей")
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), review_ids=(task_id,)))
        task = inputs.task(task_id)
        require_reviewer_identity(inputs, task, actor)
        change = task.submit(actor, payload['sections'], tuple(payload['artifact_paths']),
            payload['commit_message'], payload['content_additions'], payload['trace'],
            payload['method_additions'], payload['stage_work'], payload['evidence_work'])
        prepared = facade.prepare_change(inputs, change)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            submission_id = uow.tasks.save_prepared(prepared)
            if submission_id is None:
                raise DomainError('Нарушен контракт сохранения submission')
            return SubmissionReceipt(submission_id, change.submission is not None,
                change.task.state.version, change.task.state.submission_digest, change.registry_change)

    def validate_submission(self, task_id: str, actor: str, payload: dict) -> None:
        """Pure candidate validation before ArtifactFactory external side effects."""
        if not isinstance(payload,dict) or set(payload)!={"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Invalid stage result fields")
        if not isinstance(payload['artifact_paths'],list):
            raise DomainError('artifact_paths must be a list')
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        inputs.task(task_id).submit(actor,payload['sections'],tuple(payload['artifact_paths']),
            payload['commit_message'],payload['content_additions'],payload['trace'],
            payload['method_additions'],payload['stage_work'],payload['evidence_work'])

    def matching_submission_digest(self, task_id: str, actor: str, payload: dict) -> str | None:
        if not isinstance(payload,dict) or set(payload)!={"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Invalid stage result fields")
        if not isinstance(payload['artifact_paths'],list):
            raise DomainError('artifact_paths must be a list')
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        change = task.submit(
            actor,
            payload['sections'],
            tuple(payload['artifact_paths']),
            payload['commit_message'],
            payload['content_additions'],
            payload['trace'],
            payload['method_additions'],
            payload['stage_work'],
            payload['evidence_work'],
        )
        return task.state.submission_digest if change.submission is None else None

    def assess_content(self, task_id: str, phase: str, artifacts: tuple[ArtifactFact, ...]) -> Assessment:
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        return inputs.task(task_id).assess_content(phase, artifacts)

    def workflow_context(self, task_id: str) -> dict:
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        return inputs.task(task_id).workflow_context()

    def content_context(self, task_id: str) -> dict:
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        context = task.content_policy.describe(task.stage.stage_id, task.content_snapshot)
        contract = task._require_stage_contracts().stage(task.stage.stage_id)
        active = set(contract.entry_requirements + contract.exit_requirements)
        return {
            **context,
            "due": [item for item in context["due"] if item["id"] in active],
        }

    def mark_verified(self, task_id: str, actor: str, digest: str, report: dict,
                      artifacts: tuple[ArtifactFact, ...], *, packet_digest: str,
                      permanent_artifacts: list[dict]) -> TaskState:
        # The full original work packet, not the normalized submission payload.
        if (not isinstance(packet_digest,str) or len(packet_digest)!=64
                or any(c not in '0123456789abcdef' for c in packet_digest)):
            raise DomainError("Explicit SHA-256 work packet identity required")
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,),
            review_ids=(task_id,), verification_ids=(task_id,)))
        task=inputs.task(task_id)
        require_reviewer_identity(inputs, task, actor)
        if task.route.node(task.stage.stage_id).handler.value in ('apply_plan','publish'):
            assessed = None if task.action_assessment is None else json.loads(task.action_assessment)
            if assessed is None or assessed['tree'] != report['verified_tree']:
                raise DomainError("Action receipt is not for the verified tree")
        if task.evidence_plan.stage(task.stage.stage_id).active:
            assessed = None if task.evidence_assessment is None else json.loads(task.evidence_assessment)
            if assessed is None or assessed['tree'] != report['verified_tree']:
                raise DomainError("Доклад относится не к проверенному состоянию evidence")
        change=task.mark_verified(actor,digest,artifacts)
        if change.task.state.version == task.state.version:
            execution,_=inputs.required_execution(task_id)
            if execution["last_report"] != report:
                raise DomainError("Повтор не может заменить сохранённый доклад/receipt")
            if inputs.work_packet(task_id,task.stage.stage_id,task.state.iteration)!=packet_digest:
                raise DomainError("Saved verified work packet identity does not match")
            return task.state
        from ..modules.tasks.stage_criteria import split_work
        _, criteria_work = split_work(json.loads(task.progress.stage_work))
        if criteria_work is not None:
            producer_submission = inputs.criteria_submission_guard(task, actor, report)
            book = task.criteria_book.apply_verified_work(
                criteria_work, task, actor, producer_submission)
            change = replace(change, task=replace(change.task, criteria_book=book,
                stage_contracts=book.effective_contracts(task.stage_contracts)))
        write = facade.prepare_change(inputs, change)
        result_write = facade.prepare_result(report)
        execution, version = inputs.required_execution(task_id)
        execution_write = facade.prepare_execution(inputs, task_id,
            {**execution, 'last_report':report, 'publication':None})
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            submission_id = uow.tasks.save_prepared(write)
            if submission_id is None:
                raise DomainError("Нет submission для проверенного результата")
            uow.tasks.record_prepared_result(task_id, submission_id, result_write, actor=actor)
            uow.execution.save_prepared(task_id, execution_write, version)
            uow.artifacts.link_task(task_id, permanent_artifacts)
            uow.work_packets.remember(task_id, task.stage.stage_id, task.state.iteration, packet_digest)
        return change.task.state

    @staticmethod
    def _accept_change(
        inputs, task: Task, actor: str, advance: bool, entry_tree: str | None,
        *, force_duplicate_start=False,
    ) -> TaskState:
        require_reviewer_identity(inputs, task, actor)
        if advance and task.progress.outcome is not None:
            target = task.route.node(task.stage.stage_id).target(task.progress.outcome)
            if target is not None:
                require_reviewer_identity(inputs, task, actor, target_stage=target)
        change = task.accept(actor, advance)
        from .duplicate_tasks import require_duplicate_transition
        require_duplicate_transition(inputs,task,change,actor,
            force_duplicate_start=force_duplicate_start)
        updates = {}
        if change.task.state.version != task.state.version:
            execution, _ = inputs.required_execution(task.state.task_id)
            report = execution["last_report"]
            if report is None:
                raise DomainError("Нет сохранённого доклада проверенного этапа")
            updates["last_report"] = {
                **report,
                "status": (
                    "completed"
                    if change.task.state.status == TaskStatus.COMPLETED
                    else "accepted"
                ),
            }
            if (
                change.task.state.stage_index,
                change.task.state.iteration,
            ) != (task.state.stage_index, task.state.iteration):
                if not isinstance(entry_tree, str) or not entry_tree:
                    raise DomainError("Для нового этапа требуется наблюдаемое entry_tree")
                if entry_tree != report["verified_tree"]:
                    raise DomainError(
                        "Результат изменён после доклада: сначала явный rework, "
                        "не переход к осмотру другого дерева"
                    )
                updates.update(
                    entry_tree=entry_tree,
                    attempts=0,
                    publication=None,
                    pending=None,
                )
        return change, updates

    def progression_request(self, task_id, actor, request_id, target_stage, observe_commit):
        """Admit the actual owner before resolving immutable request identity."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise DomainError('Progression request_id is required')
        identity = _action_digest('advance', {'task_id': task_id, 'target_stage': target_stage})
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), history_ids=(task_id,)))
        task = inputs.task(task_id)
        if task.state.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            saved = inputs.progression_request(task_id, request_id, identity)
            if saved is not None and saved['result'] is not None:
                return {**saved, 'result': {**saved['result'], 'replayed': True}}
            raise DomainError('Terminal Task has no completed progression receipt for this request')
        with self.unit_of_work() as uow:
            ownership = uow.ownership.preflight(actor, task_id)
        if (task.state.claimed_by != actor or
                (inputs.worktree_required(task_id) and ownership.worktree_owner != actor) or
                task.state.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)):
            raise DomainError('Progression requires current nonterminal Task/workspace ownership')
        execution, _ = inputs.required_execution(task_id)
        owned_attempt = (is_check_attempt(execution['pending']) and task.replay_candidate is not None
                         and json.loads(task.replay_candidate)['request_id'] == request_id)
        if execution['pending'] is not None and not owned_attempt:
            raise DomainError('Progression cannot continue with a pending unknown external outcome')
        saved = inputs.progression_request(task_id, request_id, identity)
        if saved is not None:
            return saved
        if target_stage == task.stage.stage_id:
            projection = ReplayResult(ReplayIntent(task_id, request_id, target_stage),
                observe_commit(), None, None, target_stage, 'target_reached', ())
            result = {'status': 'progression_target_reached', 'replay': projection.to_dict()}
            entry = facade.prepare_progression_entry('noop',
                {'request_id':request_id, 'digest':identity, 'result':result})
            with self.unit_of_work() as uow:
                raw = uow.tasks.progression_request_raw(task_id, request_id)
                # Receipt lookup precedes source rejection; decode after closing SQL.
                if not raw.present:
                    uow.require_inputs(inputs)
                    if uow.ownership.preflight(actor, task_id) != ownership:
                        raise VersionConflict('Progression ownership changed; repeat the request')
                    uow.tasks.append_prepared_progression(task_id, actor, entry)
            replay = facade.restore_progression_request(raw, request_id, identity)
            if replay is not None:
                if replay['result'] is None:
                    raise DomainError('Progression identity already belongs to an active request')
                result = replay['result']
            return {'result':result, 'intent':None}
        return None

    def replay_context(self, task_id, request_id):
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,), history_ids=(task_id,)))
        return {'sources':inputs.accepted_replay_sources(task_id),
                'cursor':inputs.replay_progress(task_id, request_id)}

    def start_replay(self, task_id, actor, request_id, target_stage, cursor):
        identity = _action_digest('advance', {'task_id': task_id, 'target_stage': target_stage})
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), history_ids=(task_id,)))
        task = inputs.task(task_id)
        if task.state.claimed_by != actor:
            raise DomainError('Replay requires current ownership')
        if target_stage is not None and not task.route.can_reach(task.stage.stage_id, target_stage):
            raise DomainError('Progression target is not reachable')
        _, started = inputs.begin_progression(task_id, actor, request_id, identity, target_stage)
        start = None if started is None else facade.prepare_progression_entry('started', started)
        phase = facade.prepare_progression_entry('phase', {'request_id':request_id, 'cursor':cursor})
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            if start is not None:
                uow.tasks.append_prepared_progression(task_id, actor, start)
            uow.tasks.append_prepared_progression(task_id, actor, phase)

    def save_replay_progress(self, task_id, actor, request_id, cursor):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), history_ids=(task_id,)))
        if inputs.task(task_id).state.claimed_by != actor:
            raise DomainError('Replay requires current ownership')
        phase = facade.prepare_progression_entry('phase', {'request_id':request_id, 'cursor':cursor})
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.append_prepared_progression(task_id, actor, phase)

    def finish_replay(self, task_id, actor, request_id, target_stage, result):
        identity = _action_digest('advance', {'task_id':task_id, 'target_stage':target_stage})
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), history_ids=(task_id,)))
        task = inputs.task(task_id)
        if task.state.claimed_by != actor:
            raise DomainError('Replay requires current ownership')
        reached = None
        if result['status'] == 'progression_target_reached':
            current = inputs.progression_view(task_id, request_id)
            if current is None:
                raise DomainError('Unknown progression request')
            if current['status'] != 'reached':
                reached = facade.prepare_progression_entry('reached',
                    {'request_id':request_id, 'stage':result['replay']['stopped_at']})
        execution, _ = inputs.required_execution(task_id)
        write = None
        if execution['pending'] is None and result['status'] != 'progression_target_reached':
            write = facade.prepare_change(inputs, task.stop_replay_visit(actor))
        saved = inputs.progression_request(task_id, request_id, identity)
        entry = None
        if saved is not None and saved['result'] is not None:
            if saved['result'] != result:
                raise DomainError('Terminal progression result is immutable')
            result = saved['result']
        else:
            entry = facade.prepare_progression_entry('result',
                {'request_id':request_id, 'digest':identity, 'result':result})
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            if reached is not None:
                uow.tasks.append_prepared_progression(task_id, actor, reached)
            if write is not None:
                uow.tasks.save_prepared(write)
            if entry is not None:
                uow.tasks.append_prepared_progression(task_id, actor, entry)
        return deepcopy(result)

    def prepare_replay_visit(self, task_id, actor, request_id, source, commit, tree):
        candidate = {'request_id': request_id, 'stage': source['stage'],
                     'visit_id': source['visit_id'], 'source_digest': source['digest'],
                     'commit': commit, 'tree': tree,
                     'source_commit': source['commit'], 'source_tree': source['tree'],
                     'outcome': source['report']['stage_outcome'],
                     'stage_work': source['envelope']['stage_work'],
                     'evidence_work': source['envelope']['evidence_work'],
                     'evidence_plan': source['report']['evidence']['plan'],
                     'accepted_assessment': source['report']['evidence']['assessment'],
                     'method_ids': [m['id'] for m in source['methods']]}
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        change = task.prepare_replay_visit(actor, candidate)
        write = facade.prepare_change(inputs, change)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
        return change.task.check_candidate_digest

    def confirm_replay_visit(self, task_id, actor, tree, execution_key):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        if task.state.status == TaskStatus.VERIFIED and task.replay_candidate is not None:
            return
        change = task.confirm_replay_visit(actor, tree, execution_key)
        write = facade.prepare_change(inputs, change)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)

    def advance_progression(
        self,
        task_id: str,
        actor: str,
        request_id: str,
        target_stage: str | None,
        entry_tree: str,
        artifacts: tuple[ArtifactFact, ...],
        *, force_duplicate_start=False, replay_request=None,
    ) -> dict:
        from ..modules.tasks.progression import crossed_role_boundary, progression_step
        from ..modules.tasks.ports import TaskSourcesChanged
        from .duplicate_tasks import require_duplicate_start, require_duplicate_transition
        from .sprints import require_sprint_execution
        if not isinstance(request_id, str) or not request_id:
            raise DomainError('Progression request_id is required')
        identity = _action_digest('advance', {'task_id':task_id, 'target_stage':target_stage})
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), review_ids=(task_id,),
            family_ids=(task_id,), history_ids=(task_id,), related_sprints=True))
        require_sprint_execution(inputs, task_id)
        task = inputs.task(task_id)
        if task.state.claimed_by != actor:
            raise DomainError('Progression Task is owned by another session')
        execution, execution_version = inputs.required_execution(task_id)
        if execution['pending'] is not None:
            raise DomainError('Progression cannot continue with a pending unknown external outcome')
        entries = []
        started_entry = None
        change = None
        if replay_request is not None:
            cursor = inputs.replay_progress(task_id, replay_request)
            if cursor is None or task.replay_candidate is None:
                raise DomainError('Historical transition requires a prepared replay identity')
            candidate = json.loads(task.replay_candidate)
            if candidate['request_id'] != replay_request or task.state.status != TaskStatus.VERIFIED:
                raise DomainError('Historical transition requires its verified candidate')
            source = cursor['sources'][task.stage.stage_id]
            if (candidate['commit'] != cursor['start_commit'] or candidate['tree'] != cursor['current_tree']
                    or entry_tree != cursor['current_tree'] or candidate['visit_id'] != source['visit_id']
                    or candidate['source_digest'] != source['digest']):
                raise DomainError('Historical transition source drift')
            following = task.route.node(task.stage.stage_id).target(task.progress.outcome)
            if following is not None and task.route.node(following).handler.value != 'publish':
                gate = task.assess_stage_content(following, 'pre', artifacts)
                if not gate.passed:
                    return {'kind':'entry_blocked', 'gate':gate.to_dict(), 'cursor':cursor}
            cursor['passed'].append({'stage':source['stage'], 'visit_id':source['visit_id'], 'commit':source['commit']})
            if following is None:
                kind = 'task_acceptance_required'
            elif task.route.node(following).handler.value == 'publish':
                kind = 'acceptance_required'
            else:
                change = task.progress_stage(actor, following)
                require_duplicate_transition(inputs, task, change, actor,
                    force_duplicate_start=force_duplicate_start)
                kind = 'advance'
            cursor['phase'] = 'preserved'
            entries.append(facade.prepare_progression_entry('phase', {'request_id':replay_request, 'cursor':cursor}))
            result = {'kind':kind, 'following':following, 'cursor':cursor}
        else:
            require_reviewer_identity(inputs, task, actor)
            step = progression_step(task, target_stage, False)
            if step.kind == 'role_handoff_required':
                history = inputs.verified_ownership_history(task_id, task.stage.stage_id, task.state.iteration,
                    task.state.submission_digest, task.state.version)
                if crossed_role_boundary(task, actor, history):
                    require_reviewer_identity(inputs, task, actor, target_stage=step.next_stage)
                    step = progression_step(task, target_stage, True)
            require_duplicate_start(inputs, task_id, actor, force_duplicate_start=force_duplicate_start)
            progression, started = inputs.begin_progression(task_id, actor, request_id, identity, target_stage)
            if started is not None:
                started_entry = facade.prepare_progression_entry('started', started)
            if progression['status'] == 'reached' or step.kind == 'target_reached':
                if progression['status'] != 'reached':
                    entries.append(facade.prepare_progression_entry('reached',
                        {'request_id':request_id, 'stage':task.stage.stage_id}))
                result = {'kind':'target_reached', 'progression':{**progression, 'status':'reached'}}
            elif step.kind != 'advance':
                review = inputs.review_identity(task_id)
                result = {'kind':step.kind, 'progression':progression, 'step':step,
                    'review_identity':None if review is None else {**review, 'candidate_actor':actor,
                        'distinct_actor':review['executor_actor'] not in (None, actor)}}
            else:
                gate = task.assess_stage_content(step.next_stage, 'pre', artifacts)
                if not gate.passed:
                    result = {'kind':'entry_blocked', 'gate':gate.to_dict(), 'progression':progression, 'step':step}
                else:
                    report = execution['last_report']
                    if report is None:
                        raise DomainError('Stage progression requires a verified report')
                    if not isinstance(entry_tree, str) or not entry_tree:
                        raise DomainError('Stage progression requires an observable entry_tree')
                    if entry_tree != report['verified_tree']:
                        raise DomainError('The result changed after verification; use explicit rework')
                    change = task.progress_stage(actor, step.next_stage)
                    require_duplicate_transition(inputs, task, change, actor,
                        force_duplicate_start=force_duplicate_start)
                    if target_stage is not None and change.task.state.stage_index == task.route.index(target_stage):
                        entries.append(facade.prepare_progression_entry('reached',
                            {'request_id':request_id, 'stage':target_stage}))
                        result = {'kind':'target_reached', 'progression':{**progression, 'status':'reached'}}
                    else:
                        result = {'kind':'work_required', 'progression':progression}
        write = None if change is None else facade.prepare_change(inputs, change)
        execution_write = (None if change is None else facade.prepare_execution(inputs, task_id,
            {**execution, 'entry_tree':entry_tree, 'attempts':0, 'publication':None, 'pending':None}))
        try:
            with self.unit_of_work() as uow:
                uow.require_inputs(inputs)
                # The admission event precedes the Task transition in its atomic journal.
                if started_entry is not None:
                    uow.tasks.append_prepared_progression(task_id, actor, started_entry)
                if write is not None:
                    uow.tasks.save_prepared(write)
                    uow.execution.save_prepared(task_id, execution_write, execution_version)
                for entry in entries:
                    uow.tasks.append_prepared_progression(task_id, actor, entry)
        except TaskSourcesChanged as conflict:
            require_duplicate_start(conflict.current, task_id, actor, force_duplicate_start=force_duplicate_start)
            if change is not None:
                require_duplicate_transition(conflict.current, task, change, actor,
                    force_duplicate_start=force_duplicate_start)
            raise
        return result

    def accept(self, task_id: str, actor: str, advance: bool, entry_tree: str | None, *, force_duplicate_start=False) -> TaskState:
        from ..modules.tasks.ports import TaskSourcesChanged
        from .duplicate_tasks import require_duplicate_transition
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), actors=(actor,),
            execution_ids=(task_id,), review_ids=(task_id,), family_ids=(task_id,)))
        task = inputs.task(task_id)
        change, updates = self._accept_change(inputs, task, actor, advance, entry_tree,
            force_duplicate_start=force_duplicate_start)
        write = facade.prepare_change(inputs, change)
        execution, version = inputs.required_execution(task_id)
        execution_write = facade.prepare_execution(inputs, task_id, {**execution, **updates}) if updates else None
        release_worktree = (change.task.state.status == TaskStatus.COMPLETED
            and inputs.worktree_required(task_id) and inputs.ownership(actor).worktree_task_id == task_id)
        try:
            with self.unit_of_work() as uow:
                uow.require_inputs(inputs)
                uow.tasks.save_prepared(write)
                if release_worktree:
                    uow.ownership.bind_worktree(actor, None)
                if execution_write is not None:
                    uow.execution.save_prepared(task_id, execution_write, version)
        except TaskSourcesChanged as conflict:
            require_duplicate_transition(conflict.current, task, change, actor,
                force_duplicate_start=force_duplicate_start)
            raise
        return change.task.state

    def rework(self, task_id: str, actor: str, feedback: str, entry_tree: str, target: str | None = None, *, force_duplicate_start=False) -> TaskState:
        if not isinstance(entry_tree,str) or not entry_tree:
            raise DomainError("Для rework требуется наблюдаемое entry_tree")
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), family_ids=(task_id,)))
        task=inputs.task(task_id)
        execution, execution_version = inputs.required_execution(task_id)
        if execution["pending"] is not None:
            raise DomainError("Неизвестен исход прерванной операции; rework запрещён")
        change=task.rework(actor,feedback,target)
        from .duplicate_tasks import require_duplicate_transition
        require_duplicate_transition(inputs,task,change,actor,
            force_duplicate_start=force_duplicate_start)
        write = facade.prepare_change(inputs, change)
        execution_write = facade.prepare_execution(inputs, task_id,
            {**execution, 'entry_tree':entry_tree, 'attempts':0, 'publication':None, 'pending':None})
        from ..modules.tasks.ports import TaskSourcesChanged
        try:
            with self.unit_of_work() as uow:
                uow.require_inputs(inputs)
                uow.tasks.save_prepared(write)
                uow.execution.save_prepared(task_id, execution_write, execution_version)
        except TaskSourcesChanged as conflict:
            require_duplicate_transition(conflict.current, task, change, actor,
                force_duplicate_start=force_duplicate_start)
            raise
        return change.task.state

    def recover_empty_rework(self, task_id: str, reason: str, tree: str) -> dict:
        return self._recover_empty_transition(
            task_id, reason, tree, "user_rework", "empty_rework_recovered"
        )

    def recover_empty_advance(self, task_id: str, reason: str, tree: str) -> dict:
        return self._recover_empty_transition(
            task_id,
            reason,
            tree,
            "user_accept_and_continue",
            "empty_advance_recovered",
        )

    def _recover_empty_transition(
        self, task_id: str, reason: str, tree: str,
        transition_event: str, recovery_event: str,
    ) -> dict:
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), history_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        point = inputs.empty_rework_recovery_point(
            task_id, task.state.version, execution["last_report"], transition_event
        )
        if point.evidence_input is None:
            raise DomainError("Previous verified submission has no evidence input")
        if task.state.submission_digest is not None:
            raise DomainError("Current rework iteration is not empty: submission exists")
        if (
            execution["pending"] is not None
            or execution["publication"] is not None
            or execution["attempts"] != 0
            or execution["entry_tree"] != tree
            or execution["last_report"] is None
            or execution["last_report"].get("verified_tree") != tree
        ):
            raise DomainError("Execution state is not an unchanged empty rework")
        change = task.recover_empty_transition(reason, point, recovery_event)
        write = facade.prepare_change(inputs, change)
        execution_write = (facade.prepare_execution(inputs, task_id,
            {**execution, 'last_report':{**execution['last_report'], 'status':'verified'}})
            if transition_event == 'user_accept_and_continue' else None)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            if execution_write is not None:
                uow.execution.save_prepared(task_id, execution_write, version)
        return {
            "status": "recovered",
            "task": task_id,
            "stage": change.task.stage.stage_id,
            "iteration": change.task.state.iteration,
            "commit": execution["last_report"]["commit"],
            "verified_tree": tree,
        }

    def rework_failed(self, task_id: str, actor: str, feedback: str, entry_tree: str,
                      execution_key: str, target: str | None = None, *, force_duplicate_start=False) -> TaskState:
        if not isinstance(entry_tree,str) or not entry_tree or not isinstance(execution_key,str) or not execution_key:
            raise DomainError("Failed-check rework требует точные tree и execution key")
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), family_ids=(task_id,)))
        task=inputs.task(task_id)
        execution, execution_version = inputs.required_execution(task_id)
        if execution["pending"] is not None:
            raise DomainError("Неизвестен исход прерванной проверки; failed-check rework запрещён")
        change=task.rework_failed(actor,feedback,entry_tree,execution_key,target)
        from .duplicate_tasks import require_duplicate_transition
        require_duplicate_transition(inputs,task,change,actor,
            force_duplicate_start=force_duplicate_start)
        write = facade.prepare_change(inputs, change)
        execution_write = facade.prepare_execution(inputs, task_id,
            {**execution, 'entry_tree':entry_tree, 'attempts':0, 'publication':None, 'pending':None})
        from ..modules.tasks.ports import TaskSourcesChanged
        try:
            with self.unit_of_work() as uow:
                uow.require_inputs(inputs)
                uow.tasks.save_prepared(write)
                uow.execution.save_prepared(task_id, execution_write, execution_version)
        except TaskSourcesChanged as conflict:
            require_duplicate_transition(conflict.current, task, change, actor,
                force_duplicate_start=force_duplicate_start)
            raise
        return change.task.state

    def inspection_registry_rework_available(self, task_id: str, target: str | None) -> bool:
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        return task.inspection_registry_blocker(target) is not None

    def failed_observation_batch(self, task_id, tree, execution_key):
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task=inputs.task(task_id)
        return task.evidence_book.failed_batch(
            task.stage.stage_id,task.state.iteration,task.state.submission_digest,tree,execution_key)

    def cancel_newborn(self, task_id: str, actor: str, reason: str) -> TaskState:
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), actors=(actor,)))
        newborn = inputs.newborn(task_id)
        state = newborn.cancellation(actor, reason)
        if inputs.execution(task_id) is not None:
            raise DomainError("Newborn Task has historical execution; cancellation is not planning-only")
        release_tree = (inputs.worktree_required(task_id)
                        and inputs.ownership(actor).worktree_task_id == task_id)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            if release_tree:
                uow.ownership.bind_worktree(actor, None)
            uow.tasks.cancel_newborn(state, newborn.version, actor, reason)
        return state

    def cancel(self, task_id: str, actor: str, reason: str, cleanup: dict) -> TaskState:
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), actors=(actor,)))
        task = inputs.task(task_id)
        change = task.cancel(actor, reason)
        execution, version = inputs.required_execution(task_id)
        if execution['pending'] is not None:
            raise DomainError('External operation outcome must be resolved first')
        write = facade.prepare_change(inputs, change)
        execution_write = facade.prepare_execution(inputs, task_id, {**execution, 'pending':cleanup})
        release_tree = (inputs.worktree_required(task_id)
                        and inputs.ownership(actor).worktree_task_id == task_id)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            if release_tree:
                uow.ownership.bind_worktree(actor, None)
            uow.execution.save_prepared(task_id, execution_write, version)
        return change.task.state

    def proof_submission_context(self, task_id, actor, work, tree):
        """Validate through the Task owner before composition observes files."""
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        task._owned(actor)
        task.evidence_book.validate_submission(
            task.evidence_plan, task.stage.stage_id, work,
            task.check_registry.obligation_catalog, tree)
        return task.evidence_book.context()

    def evidence_precheck(self, task_id, actor, tree, execution_key):
        import json
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task=inputs.task(task_id)
        task._owned(actor)
        if task.evidence_input is None:
            raise DomainError('Нет evidence submission')
        return task.evidence_book.precheck(task.evidence_plan,task.stage.stage_id,task.state.iteration,
                                           tree,execution_key,json.loads(task.evidence_input))

    def observation_batch(self, task_id, tree, execution_key):
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task=inputs.task(task_id)
        return task.evidence_book.batch(task.stage.stage_id,task.state.iteration,tree,execution_key)

    def submission_observation_batch(
        self, task_id, submission_digest, tree, execution_key
    ):
        inputs = self.unit_of_work().capture(TaskReadSet((task_id,)))
        task=inputs.task(task_id)
        return task.evidence_book.submission_batch(
            task.stage.stage_id,task.state.iteration,
            submission_digest,tree,execution_key
        )

    def record_observations(self, task_id, actor, tree, execution_key, receipts):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,), evidence_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        attempt = is_check_attempt(execution['pending'])
        if attempt:
            change = CheckAttempts.finish(task, actor, execution, inputs.evidence(task_id),
                                          tree, execution_key, receipts)
            execution_write = facade.prepare_execution(inputs, task_id, {**execution, 'pending':None})
        else:
            if execution['pending'] is not None:
                raise DomainError('Cannot finalize observations over another pending operation')
            change = task.record_observations(actor,tree,execution_key,receipts)
        write = facade.prepare_change(inputs, change)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            if attempt:
                uow.execution.save_prepared(task_id, execution_write, version)

    def begin_check_attempt(self, task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest, identity):
        return CheckAttempts(self.unit_of_work).begin(task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest, identity)

    def current_check_attempt(self, task_id, actor, tree, execution_key, methods):
        return CheckAttempts(self.unit_of_work).current(task_id, actor, tree, execution_key, methods)

    def start_check_run(self, task_id, actor, expected, run_id):
        return CheckAttempts(self.unit_of_work).start_run(task_id, actor, expected, run_id)

    def record_check_termination(self, task_id, actor, expected, run_id, outcome):
        return CheckAttempts(self.unit_of_work).record_termination(task_id, actor, expected, run_id, outcome)

    def recover_pending_checks(
        self, task_id, actor, submission_digest, tree, execution_key, receipts
    ):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,), execution_ids=(task_id,)))
        task = inputs.task(task_id)
        execution, execution_version = inputs.required_execution(task_id)
        if execution['pending'] != 'checks':
            raise DomainError("Check recovery requires pending=checks")
        if task.state.submission_digest != submission_digest:
            raise DomainError("Check recovery submission changed before commit")
        batch = task.evidence_book.submission_batch(
            task.stage.stage_id,
            task.state.iteration,
            submission_digest,
            tree,
            execution_key,
        )
        if batch is None or batch['receipts'] != receipts:
            raise DomainError("Check recovery receipts changed before commit")
        change = task.recover_pending_checks(actor)
        write = facade.prepare_change(inputs, change)
        execution_write = facade.prepare_execution(inputs, task_id, {**execution, 'pending':None})
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
            uow.execution.save_prepared(task_id, execution_write, execution_version)

    def assess_evidence(self, task_id, actor, tree, execution_key):
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        change, assessment = task.assess_evidence(actor, tree, execution_key)
        write = facade.prepare_change(inputs, change)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.tasks.save_prepared(write)
        return assessment.to_dict()
