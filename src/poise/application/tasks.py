from __future__ import annotations
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from ..modules.tasks.domain import (
    Task,
    TaskStageContracts,
    TaskState,
    TaskStatus,
)
from ..modules.workflow.domain import RouteDefinition
from ..modules.tasks.ports import RepositoryTreeReader, TaskUnitOfWork
from ..modules.tasks.contracts import stages_from_process, evidence_plan_from_metadata
from ..modules.verification.domain import CheckRegistry
from ..modules.content_requirements.domain import ArtifactFact, Assessment
from ..modules.foundation.errors import DomainError, VersionConflict
from ..modules.tasks.newborn import NewbornTask
from ..modules.tasks.definition import build_task, validate_creation
from .check_attempts import CheckAttempts, is_check_attempt


def require_reviewer_identity_in(
    uow: TaskUnitOfWork, task: Task, actor: str, *,
    target_stage: str | None = None, acquiring: bool = False,
) -> dict | None:
    """One guard for public acquisition, handoff and progression owners."""
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
    identity = uow.tasks.review_identity(task.state.task_id)
    if identity is None:
        return None  # An inspection route may begin with externally supplied input.
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
    intent, allocation, actor, process, automatic_checks, decomposition_policy,
    base_metadata,
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
        contract, process, automatic_checks, decomposition_policy
    )
    metadata.update(base_metadata)
    metadata.update(sprint_id=contract['sprint_id'], goal=contract['goal'])
    if creation_request is not None:
        metadata['creation_request'] = creation_request
    return build_task(metadata, actor), metadata, contract


@dataclass(frozen=True)
class PreparedCreation:
    source_intent: dict
    intent: dict
    process: dict
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
    process = deepcopy(prepared.process)
    automatic_checks = deepcopy(prepared.automatic_checks)
    decomposition_policy = deepcopy(prepared.decomposition_policy)
    request_id, _, _ = creation_parts(source_intent)
    parsed_policy = None if request_id is None else TaskIdPolicy.parse(policy)
    allocation = uow.tasks.allocate(source_intent, parsed_policy, reserved_ids)
    task, metadata, contract = _creation_values(
        intent, allocation, None, process, automatic_checks, decomposition_policy,
        base_metadata
    )
    if not allocation.replayed:
        uow.tasks.create(task, metadata)
    prepared.requirements_gate.publish_created(
        uow, allocation.task_id, prepared.requirements_context
    )
    return allocation, contract


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


def validate_creation_intent(intent, process, automatic_checks, decomposition_policy):
    from ..modules.tasks.allocation import creation_alias, materialize_contract
    from ..modules.tasks.definition import validate_creation
    contract, _ = materialize_contract(intent, creation_alias(intent))
    return validate_creation(contract, process, automatic_checks, decomposition_policy)


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


def require_start_execution(execution, restart_history):
    """One execution prerequisite for start and read-only discovery."""
    if execution is not None:
        if not restart_history:
            raise DomainError('Available Task has unexpected execution state')
        if execution['pending'] is not None:
            raise DomainError('Restarted Task execution recovery is still pending')


class TaskCommands:
    """One application API for Task changes. Every call uses a short UoW."""
    def __init__(self, unit_of_work: Callable[[], TaskUnitOfWork], repository_tree: RepositoryTreeReader,
                 requirements_gate):
        self.unit_of_work = unit_of_work
        self.repository_tree = repository_tree
        self.requirements_gate = requirements_gate

    def reviewer_preflight(self, task_id: str, actor: str, *, acquiring: bool = False) -> dict | None:
        """Reject self-review before filesystem preparation; writes recheck in their UoW."""
        with self.unit_of_work() as uow:
            if uow.tasks.is_newborn(task_id):
                return None
            return require_reviewer_identity_in(
                uow, uow.tasks.load(task_id), actor, acquiring=acquiring,
            )

    def require_bootstrap_contract(self, task_id: str, contract: dict) -> None:
        """Accept only an exact saved canonical or full approved contract."""
        with self.unit_of_work() as uow:
            saved = uow.tasks.restart_context(task_id)
        if contract == saved['contract']:
            return  # Preserve the existing public canonical-contract continuation.
        candidate, _ = self.requirements_gate.prepare_restarted_contract(
            contract, saved['requirements_snapshot'], saved['requirements_agreement']
        )
        if candidate != saved['contract']:
            raise DomainError('Existing task contract is immutable; bootstrap is not an editor')

    def prepare_creation(self, intent, process, automatic_checks, base_revision, decomposition_policy):
        return self._prepare_creation(
            intent,
            process,
            automatic_checks,
            base_revision,
            decomposition_policy,
            self.requirements_gate.prepare_contract,
        )

    def _prepare_restarted_creation(
        self, intent, process, automatic_checks, base_revision, decomposition_policy, persisted_context
    ):
        return self._prepare_creation(
            intent,
            process,
            automatic_checks,
            base_revision,
            decomposition_policy,
            lambda candidate: self.requirements_gate.prepare_restarted_contract(
                candidate,
                persisted_context["requirements_snapshot"],
                persisted_context["requirements_agreement"],
            ),
        )

    def _prepare_creation(
        self, intent, process, automatic_checks, base_revision, decomposition_policy, prepare_requirements
    ):
        from ..modules.tasks.allocation import creation_alias, materialize_contract
        from ..modules.tasks.creation_preflight import CreationPreflight
        from ..modules.tasks.definition import validate_creation
        source_intent = deepcopy(intent)
        intent, requirements_context = prepare_requirements(intent)
        candidate, _ = materialize_contract(intent, creation_alias(intent))
        try:
            preflight = CreationPreflight.parse(candidate, process)
        except KeyError as exc:
            validate_creation(
                candidate, process, automatic_checks, decomposition_policy
            )
            raise DomainError(
                f"Task creation preflight requires field {exc.args[0]!r}"
            ) from exc
        if not isinstance(base_revision, str) or not base_revision:
            raise DomainError("Task creation requires an explicit repository tree preflight")
        preflight.validate_base(
            self.repository_tree.existing_paths(base_revision, preflight.repository_inputs)
        )
        validate_creation(candidate, process, automatic_checks, decomposition_policy)
        return PreparedCreation(
            source_intent,
            deepcopy(intent),
            deepcopy(process),
            deepcopy(automatic_checks),
            deepcopy(decomposition_policy),
            deepcopy(requirements_context),
            self.requirements_gate,
        )

    def publish_requirements(self, task_id, task_requirements, agreement):
        return self.requirements_gate.publish(task_id, task_requirements, agreement)

    def requirements_snapshot(self, task_id):
        return self.requirements_gate.snapshot(task_id)

    def create(self, intent: dict, actor: str, process: dict, automatic_checks: list,
               base_metadata: dict, execution, policy, base_revision,
               decomposition_policy):
        from ..application.ownership import release_task_in
        from ..modules.tasks.allocation import TaskIdPolicy, creation_parts
        request_id, _, _ = creation_parts(intent)
        parsed_policy = None if request_id is None else TaskIdPolicy.parse(policy)
        with self.unit_of_work() as uow:
            replay = uow.tasks.allocate(intent, parsed_policy)
        if replay.replayed:
            return replay
        prepared = self.prepare_creation(
            intent, process, automatic_checks, base_revision, decomposition_policy
        )
        with self.unit_of_work() as uow:
            allocation = uow.tasks.allocate(intent, parsed_policy)
            task, metadata, _ = _creation_values(
                prepared.intent,
                allocation,
                None,
                prepared.process,
                prepared.automatic_checks,
                prepared.decomposition_policy,
                base_metadata,
            )
            if not allocation.replayed:
                before = uow.ownership.snapshot(actor)
                if before.task_id is not None:
                    release_task_in(uow, actor, before.task_id)
                newborn = NewbornTask.create(allocation.task_id, None, actor)
                uow.tasks.create_newborn(newborn, base_metadata['config_hash'])
                uow.tasks.promote_newborn(task, metadata, newborn.version)
            self.requirements_gate.publish_created(
                uow, allocation.task_id, prepared.requirements_context
            )
            return allocation

    def create_duplicate(self, task_id, parent_id, sprint_id, actor, config_hash, request_id):
        from .duplicate_tasks import create_duplicate_in
        identity = _action_digest('duplicate', {
            'task_id':task_id,'parent_id':parent_id,'sprint_id':sprint_id})
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id,request_id,identity)
            if replay is not None:
                return replay
            result = create_duplicate_in(uow,task_id,parent_id,sprint_id,actor,config_hash)
            uow.tasks.remember_action(task_id,actor,request_id,identity,result)
            return result

    def create_newborn(self, task_id, sprint_id, actor, config_hash, request_id):
        from ..application.ownership import release_task_in
        from ..modules.sprints.domain import Sprint
        identity = _action_digest("create", {"task_id": task_id, "sprint_id": sprint_id})
        newborn = NewbornTask.create(task_id, sprint_id, actor)
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return replay
            before = uow.ownership.snapshot(actor)
            if before.task_id not in (None, task_id):
                release_task_in(uow, actor, before.task_id)
            sprint_revision = None
            if sprint_id is not None:
                record = uow.sprints.get(sprint_id)
                if record is None:
                    raise DomainError('Unknown Sprint for newborn membership')
                sprint = Sprint.restore(record['aggregate']).add_newborn_member(task_id)
                record = {**record, 'actor':actor, 'aggregate':sprint.to_dict()}
                uow.tasks.create_newborn(newborn, config_hash)
                uow.sprints.save(record, sprint.revision - 1)
                sprint_revision = sprint.revision
            else:
                uow.tasks.create_newborn(newborn, config_hash)
            result = newborn.describe() | ({'sprint_revision':sprint_revision} if sprint_id is not None else {})
            uow.tasks.remember_action(task_id, actor, request_id, identity, result)
            return result

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
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return {**replay, "replayed": True}
            task = uow.tasks.load(task_id)
            if task.state.version != expected_version:
                raise VersionConflict("Cancellation recovery version changed")
            if task.state.status != TaskStatus.CANCELLED:
                raise DomainError("Recovery requires a cancelled unfinished Task")
            ownership = uow.ownership.preflight(actor, task_id)
            if ownership.task_owner not in (None, actor) or ownership.worktree_owner not in (None, actor):
                raise DomainError("Recovery target is owned by another session")
            before = uow.ownership.snapshot(actor)
            if before.task_id not in (None, task_id):
                raise DomainError("Recovery requires an idle or owning session")
            previous = uow.tasks.cancellation_recovery_point(task_id, expected_version)
            change = task.recover_cancelled(actor, previous, reason, authorization)
            execution = None
            pending = None
            if uow.execution.exists(task_id):
                execution, execution_version = uow.execution.load(task_id)
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
            uow.tasks.save(change, expected_version)
            if pending is not None:
                uow.execution.save(task_id, {**execution, "pending": None}, execution_version)
            result = {"status": "task_recovered", "task": task_id,
                      "task_status": change.task.state.status.value,
                      "version": change.task.state.version, "request_id": request_id,
                      "authorization": authorization, "reason": reason,
                      "withdrawn_cleanup": deepcopy(pending), "replayed": False}
            uow.tasks.remember_action(task_id, actor, request_id, identity, result)
            return result

    def restart_newborn(
        self, task_id, actor, expected_version, request_id, reason, authorization,
        *, validate_reuse_restart=None, validate_check_restart=None,
    ):
        from ..application.ownership import release_task_in
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
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return {**replay, "replayed": True}
            task = uow.tasks.load(task_id)
            if task.state.version != expected_version:
                raise VersionConflict("Task restart version changed")
            target = uow.ownership.preflight(actor, task_id)
            if target.task_owner not in (None, actor):
                raise DomainError("Task is owned by another session; handoff is required")
            if target.worktree_owner not in (None, actor):
                raise DomainError("Task worktree is owned by another session; handoff is required")
            context = uow.tasks.restart_context(task_id)
            restart_contract = deepcopy(context["contract"])
            if context["requirements_snapshot"] is not None:
                restart_contract["requirements_snapshot"] = deepcopy(
                    context["requirements_snapshot"]
                )
                restart_contract["requirements_agreement"] = deepcopy(
                    context["requirements_agreement"]
                )
            from .duplicate_tasks import restart_local_repair_in
            recovery = {}
            local_repair = restart_local_repair_in(
                uow, task, context['restart_history'], validate_reuse_restart)
            if local_repair is not None:
                recovery['local_repair'] = local_repair
            if uow.execution.exists(task_id):
                abandoned = CheckAttempts.abandon_for_restart_in(
                    uow, task, actor, validate_check_restart)
                if abandoned is not None:
                    recovery['abandoned_check_attempt'] = abandoned
            newborn = NewbornTask.restart(
                task,
                restart_contract,
                context["process"],
                context["sprint_id"],
                actor,
                reason,
                authorization,
                context["restart_history"],
                context["creation_request"],
                context["stage_contract_history"],
                recovery=recovery,
            )
            before = uow.ownership.snapshot(actor)
            if before.task_id not in (None, task_id):
                release_task_in(uow, actor, before.task_id)
            uow.work_packets.invalidate(task_id)
            if uow.execution.exists(task_id):
                uow.execution.restart(task_id)
            uow.tasks.restart_newborn(
                newborn,
                expected_version,
                context["config_hash"],
                reason,
                authorization,
            )
            if uow.execution.exists(task_id) and context["process"]["worktree_required"]:
                uow.ownership.bind_worktree(actor, task_id)
            result = {**newborn.describe(), "replayed": False}
            uow.tasks.remember_action(task_id, actor, request_id, identity, result)
            return result

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
        with self.unit_of_work() as uow:
            replay = uow.tasks.stage_contract_receipt(task_id, request_id, audit)
            if replay is not None:
                return replay
            self._stage_contract_authorization(authorization, "creator")
            task = uow.tasks.load(task_id)
            if task.state.version != expected_version:
                raise DomainError("stage contract version conflict")
            parsed = TaskStageContracts.parse(contracts, task.route, task.content_policy)
            change = task.initialize_stage_contracts(parsed)
            return uow.tasks.save_stage_contract_change(change, expected_version, audit)

    def revise_stage_contract(
        self, task_id, actor, expected_version, request_id, stage_id, contract,
        reason, authorization
    ):
        from ..application.ownership import release_task_in
        if not isinstance(request_id, str) or not request_id:
            raise DomainError("stage contract request_id is required")
        if type(expected_version) is not int or expected_version < 0:
            raise DomainError("stage contract expected_version must be a nonnegative integer")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("stage contract revision reason is required")
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
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
            replay = uow.tasks.stage_contract_receipt(task_id, request_id, audit)
            if replay is not None:
                return replay
            self._stage_contract_authorization(authorization, "reviewer")
            if task.state.version != expected_version:
                raise DomainError("stage contract version conflict")
            target = uow.ownership.preflight(actor, task_id)
            if target.task_owner not in (None, actor):
                raise DomainError("Stage contract Task is owned by another session; handoff is required")
            if target.worktree_owner not in (None, actor):
                raise DomainError("Stage contract worktree is owned by another session; handoff is required")
            before = uow.ownership.snapshot(actor)
            if before.task_id not in (None, task_id):
                release_task_in(uow, actor, before.task_id)
            if not isinstance(contract, dict) or set(contract) != {
                "stage_id", "allowed_paths", "entry_requirements", "exit_requirements"
            } or contract["stage_id"] != stage_id:
                raise DomainError("Exact replacement stage contract is required")
            candidate = [
                deepcopy(contract) if item.stage_id == stage_id else item.to_dict()
                for item in task.stage_contracts.items
            ]
            replacement = TaskStageContracts.parse(
                candidate, task.route, task.content_policy
            ).stage(stage_id)
            change = task.revise_stage_contract(actor, stage_id, replacement)
            result = uow.tasks.save_stage_contract_change(change, expected_version, audit)
            if uow.ownership.worktree_required(task_id):
                uow.ownership.bind_worktree(actor, task_id)
            return result

    def stage_contract_context(self, task_id):
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            task._require_stage_contracts()
            return uow.tasks.stage_contract_context(task_id)

    def edit_newborn(self, task_id, actor, expected_revision, patch, remove, processes,
                     config_hash, request_id):
        from ..application.ownership import release_task_in
        if type(expected_revision) is not int:
            raise DomainError('Newborn edit requires expected_revision')
        identity = _action_digest("edit", {
            "task_id": task_id,
            "expected_revision": expected_revision,
            "patch": patch,
            "remove": remove,
        })
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return replay
            newborn = uow.tasks.load_newborn(task_id)
            if newborn.version != expected_revision:
                from ..modules.foundation.errors import VersionConflict
                raise VersionConflict('Newborn Task revision changed')
            if newborn.claimed_by not in (None, actor):
                raise DomainError('Newborn Task is owned by another session')
            before = uow.ownership.snapshot(actor)
            if before.task_id not in (None, task_id):
                release_task_in(uow, actor, before.task_id)
            changed = newborn.edit(patch, remove, processes, actor)
            uow.tasks.save_newborn(changed, newborn.version, config_hash, 'newborn_edited')
            result = changed.describe()
            uow.tasks.remember_action(task_id, actor, request_id, identity, result)
            return result

    def ready_newborn(self, task_id, actor, expected_revision, automatic_checks,
                      decomposition_policy, config_hash, request_id, creation_base):
        if type(expected_revision) is not int:
            raise DomainError('Newborn ready requires expected_revision')
        identity = _action_digest("ready", {
            "task_id": task_id, "expected_revision": expected_revision,
        })
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return replay
            newborn = uow.tasks.load_newborn(task_id)
            if newborn.version != expected_revision:
                from ..modules.foundation.errors import VersionConflict
                raise VersionConflict('Newborn Task revision changed')
            if newborn.claimed_by != actor:
                raise DomainError('Newborn ready requires current ownership')
            if newborn.process is None:
                raise DomainError('Select goal_type before ready')
            contract = {'id':task_id, 'sprint_id':newborn.sprint_id, **deepcopy(newborn.draft)}
            snapshot = newborn
            context = uow.tasks.restart_context(task_id)
            effective_hash = context['config_hash'] if newborn.restart_history else config_hash
            effective_checks = automatic_checks
            effective_decomposition = decomposition_policy
            restart_base = None
            if newborn.restart_history and uow.execution.exists(task_id):
                restart_base = uow.execution.load(task_id)[0]['base']
            if newborn.sprint_id is not None and newborn.restart_history:
                sprint = uow.sprints.get(newborn.sprint_id)
                if sprint is None or sprint['aggregate']['state'] != 'published':
                    raise DomainError('Restarted Sprint Task requires its published Sprint')
                effective_checks = sprint['automatic_checks']
                effective_decomposition = sprint['task_decomposition']
        base_revision = restart_base if restart_base is not None else creation_base()
        if newborn.restart_history:
            prepared = self._prepare_restarted_creation(
                contract,
                snapshot.process,
                effective_checks,
                base_revision,
                effective_decomposition,
                context,
            )
        else:
            prepared = self.prepare_creation(
                contract,
                snapshot.process,
                effective_checks,
                base_revision,
                effective_decomposition,
            )
        with self.unit_of_work() as uow:
            replay = uow.tasks.action_receipt(task_id, request_id, identity)
            if replay is not None:
                return replay
            newborn = uow.tasks.load_newborn(task_id)
            if newborn != snapshot:
                raise VersionConflict('Newborn Task changed after creation preflight')
            metadata = validate_creation(
                prepared.intent, newborn.process, effective_checks,
                effective_decomposition,
            )
            metadata.update(sprint_id=newborn.sprint_id, goal=contract['goal'], config_hash=effective_hash)
            if newborn.creation_request is not None:
                metadata['creation_request'] = deepcopy(newborn.creation_request)
            if newborn.restart_history:
                metadata['restart_history'] = list(deepcopy(newborn.restart_history))
            if newborn.stage_contract_history:
                metadata['stage_contract_history'] = list(
                    deepcopy(newborn.stage_contract_history)
                )
            if newborn.sprint_id is not None and not newborn.restart_history:
                ready = newborn.mark_ready()
                uow.tasks.save_newborn(ready, newborn.version, effective_hash, 'newborn_ready')
                result = ready.describe()
                uow.tasks.remember_action(task_id, actor, request_id, identity, result)
                return result
            task = build_task(metadata, None)
            uow.tasks.promote_newborn(task, metadata, newborn.version)
            self.requirements_gate.publish_created(
                uow, task_id, prepared.requirements_context
            )
            if newborn.restart_history and restart_base is not None:
                from ..application.ownership import release_dependent_worktree_in
                release_dependent_worktree_in(uow, actor, task_id)
            result = {
                'status':'available','task':task_id,'revision':newborn.version + 1,
                'sprint':newborn.sprint_id,'claimed_by':None,'goal_type':contract['goal_type'],
                'route_entry':newborn.process['route']['entry'],'ready':True,
            }
            uow.tasks.remember_action(task_id, actor, request_id, identity, result)
            return result

    def read_duplicate_family(self, task_id):
        with self.unit_of_work() as uow:
            return uow.tasks.read_duplicate_family(task_id)

    def duplicate_preflight(self, task_id, actor, *, force_duplicate_start=False):
        from .duplicate_tasks import require_duplicate_start_in
        with self.unit_of_work() as uow:
            return require_duplicate_start_in(uow, task_id, actor,
                force_duplicate_start=force_duplicate_start)

    def start(self, task_id, actor, execution, *, force_duplicate_start=False):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            from .duplicate_tasks import require_duplicate_start_in
            duplicate = require_duplicate_start_in(uow, task_id, actor,
                force_duplicate_start=force_duplicate_start)
            has_execution = uow.execution.exists(task_id)
            if not has_execution and duplicate is not None and duplicate['reason'] == 'local_repair':
                if self.repository_tree is None or not self.repository_tree.contains_commit(
                        execution['base'], duplicate['source']['source_commit']):
                    raise DomainError('Local repair source is absent from reserved base; import it before start')
            if has_execution:
                current, _ = uow.execution.load(task_id)
                require_start_execution(current, uow.tasks.restart_context(task_id)['restart_history'])
            change=task.start(actor)
            uow.tasks.save(change,task.state.version)
            started=change.task
            # Reserve a duplicate's claim in the admission transaction. Otherwise
            # a second family start could slip in during worktree preparation.
            if duplicate is None:
                uow.tasks.save(started.release_ownership(actor),started.state.version)
            if not has_execution:
                uow.execution.create(task_id,execution)

    def submit(self, task_id: str, actor: str, payload: dict) -> SubmissionReceipt:
        if not isinstance(payload, dict) or set(payload) != {"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Требуется точный stage result: sections, artifact_paths, commit_message, content_additions, trace, method_additions, stage_work, evidence_work")
        if not isinstance(payload["artifact_paths"],list):
            raise DomainError("artifact_paths должен быть списком путей")
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            require_reviewer_identity_in(uow, task, actor)
            change = task.submit(actor, payload["sections"], tuple(payload["artifact_paths"]), payload["commit_message"],
                                 payload["content_additions"], payload["trace"], payload["method_additions"], payload["stage_work"], payload["evidence_work"])
            submission_id = uow.tasks.save(change, task.state.version)
            if submission_id is None:
                raise DomainError("Нарушен контракт сохранения submission")
            return SubmissionReceipt(submission_id, change.submission is not None,
                                     change.task.state.version, change.task.state.submission_digest,
                                     change.registry_change)

    def validate_submission(self, task_id: str, actor: str, payload: dict) -> None:
        """Pure candidate validation before ArtifactFactory external side effects."""
        if not isinstance(payload,dict) or set(payload)!={"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Invalid stage result fields")
        if not isinstance(payload['artifact_paths'],list):
            raise DomainError('artifact_paths must be a list')
        with self.unit_of_work() as uow:
            uow.tasks.load(task_id).submit(actor,payload['sections'],tuple(payload['artifact_paths']),
                payload['commit_message'],payload['content_additions'],payload['trace'],
                payload['method_additions'],payload['stage_work'],payload['evidence_work'])

    def matching_submission_digest(self, task_id: str, actor: str, payload: dict) -> str | None:
        if not isinstance(payload,dict) or set(payload)!={"sections","artifact_paths","commit_message","content_additions","trace","method_additions","stage_work","evidence_work"}:
            raise DomainError("Invalid stage result fields")
        if not isinstance(payload['artifact_paths'],list):
            raise DomainError('artifact_paths must be a list')
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
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
        with self.unit_of_work() as uow:
            return uow.tasks.load(task_id).assess_content(phase, artifacts)

    def workflow_context(self, task_id: str) -> dict:
        with self.unit_of_work() as uow:
            return uow.tasks.load(task_id).workflow_context()

    def content_context(self, task_id: str) -> dict:
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
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
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            require_reviewer_identity_in(uow, task, actor)
            if task.route.node(task.stage.stage_id).handler.value in ('apply_plan','publish'):
                import json
                assessed = None if task.action_assessment is None else json.loads(task.action_assessment)
                if assessed is None or assessed['tree'] != report['verified_tree']:
                    raise DomainError("Action receipt is not for the verified tree")
            if task.evidence_plan.stage(task.stage.stage_id).active:
                import json
                assessed = None if task.evidence_assessment is None else json.loads(task.evidence_assessment)
                if assessed is None or assessed['tree'] != report['verified_tree']:
                    raise DomainError("Доклад относится не к проверенному состоянию evidence")
            change=task.mark_verified(actor,digest,artifacts)
            if change.task.state.version == task.state.version:
                execution,_=uow.execution.load(task_id)
                if execution["last_report"] != report:
                    raise DomainError("Повтор не может заменить сохранённый доклад/receipt")
                if uow.work_packets.current(task_id,task.stage.stage_id,task.state.iteration)!=packet_digest:
                    raise DomainError("Saved verified work packet identity does not match")
                return task.state
            submission_id = uow.tasks.save(change,task.state.version)
            if submission_id is None:
                raise DomainError("Нет submission для проверенного результата")
            uow.tasks.record_result(task_id, submission_id, report, actor=actor)
            uow.execution.patch(task_id,{"last_report":report,"publication":None})
            uow.artifacts.link_task(task_id,permanent_artifacts)
            uow.work_packets.remember(task_id,task.stage.stage_id,task.state.iteration,packet_digest)
            return change.task.state

    @staticmethod
    def _accept_in_uow(
        uow, task: Task, actor: str, advance: bool, entry_tree: str | None,
        *, force_duplicate_start=False,
    ) -> TaskState:
        require_reviewer_identity_in(uow, task, actor)
        if advance and task.progress.outcome is not None:
            target = task.route.node(task.stage.stage_id).target(task.progress.outcome)
            if target is not None:
                require_reviewer_identity_in(uow, task, actor, target_stage=target)
        change = task.accept(actor, advance)
        from .duplicate_tasks import require_duplicate_transition_in
        require_duplicate_transition_in(uow,task,change,actor,
            force_duplicate_start=force_duplicate_start)
        updates = {}
        if change.task.state.version != task.state.version:
            execution, _ = uow.execution.load(task.state.task_id)
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
        uow.tasks.save(change, task.state.version)
        uow.execution.patch(task.state.task_id, updates)
        return change.task.state

    def advance_progression(
        self,
        task_id: str,
        actor: str,
        request_id: str,
        target_stage: str,
        entry_tree: str,
        artifacts: tuple[ArtifactFact, ...],
        *, force_duplicate_start=False,
    ) -> dict:
        from ..modules.tasks.progression import progression_step
        from .duplicate_tasks import require_duplicate_start_in, require_duplicate_transition_in

        if not isinstance(request_id, str) or not request_id:
            raise DomainError("Progression request_id is required")
        identity = _action_digest("advance", {
            "task_id": task_id,
            "target_stage": target_stage,
        })
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            if task.state.claimed_by != actor:
                raise DomainError("Progression Task is owned by another session")
            execution, _ = uow.execution.load(task_id)
            if execution["pending"] is not None:
                raise DomainError(
                    "Progression cannot continue with a pending unknown external outcome"
                )
            handoff = uow.handoffs.latest_recovery_candidate(task_id)
            ownership_suffix = (
                ()
                if handoff is None
                else uow.tasks.ownership_event_suffix(
                    task_id, handoff["version"], task.state.version
                )
            )
            resumed_boundary = bool(
                handoff is not None
                and handoff["state"] == "resumed"
                and handoff["receipt"]["stage"] == task.stage.stage_id
                and ownership_suffix[:2] == ("handed_off", "handoff_resumed")
            )
            crossed = resumed_boundary and handoff["actor"] != actor
            require_reviewer_identity_in(uow, task, actor)
            if resumed_boundary and task.progress.outcome is not None:
                following = task.route.node(task.stage.stage_id).target(task.progress.outcome)
                if following is not None:
                    require_reviewer_identity_in(uow, task, actor, target_stage=following)
            step = progression_step(task, target_stage, crossed)
            require_duplicate_start_in(uow,task_id,actor,force_duplicate_start=force_duplicate_start)
            progression = uow.tasks.begin_progression(
                task_id, actor, request_id, identity, target_stage
            )
            if progression["status"] == "reached" or step.kind == "target_reached":
                uow.tasks.finish_progression(
                    task_id, actor, request_id, task.stage.stage_id
                )
                return {"kind": "target_reached", "progression": {
                    **progression, "status": "reached",
                }}
            if step.kind != "advance":
                identity = uow.tasks.review_identity(task_id)
                return {"kind": step.kind, "progression": progression, "step": step,
                        "review_identity": None if identity is None else {
                            **identity, "candidate_actor": actor,
                            "distinct_actor": identity["executor_actor"] not in (None, actor),
                        }}
            gate = task.assess_stage_content(step.next_stage, "pre", artifacts)
            if not gate.passed:
                return {
                    "kind": "entry_blocked",
                    "gate": gate.to_dict(),
                    "progression": progression,
                    "step": step,
                }
            report = execution["last_report"]
            if report is None:
                raise DomainError("Stage progression requires a verified report")
            if not isinstance(entry_tree, str) or not entry_tree:
                raise DomainError("Stage progression requires an observable entry_tree")
            if entry_tree != report["verified_tree"]:
                raise DomainError(
                    "The result changed after verification; use explicit rework"
                )
            change = task.progress_stage(actor, step.next_stage)
            require_duplicate_transition_in(uow,task,change,actor,
                force_duplicate_start=force_duplicate_start)
            uow.tasks.save(change, task.state.version)
            uow.execution.patch(task_id, {
                "entry_tree": entry_tree,
                "attempts": 0,
                "publication": None,
                "pending": None,
            })
            state = change.task.state
            if state.stage_index == task.route.index(target_stage):
                uow.tasks.finish_progression(task_id, actor, request_id, target_stage)
                progression = {**progression, "status": "reached"}
                return {"kind": "target_reached", "progression": progression}
            return {"kind": "work_required", "progression": progression}

    def accept(self, task_id: str, actor: str, advance: bool, entry_tree: str | None, *, force_duplicate_start=False) -> TaskState:
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return self._accept_in_uow(uow, task, actor, advance, entry_tree,force_duplicate_start=force_duplicate_start)

    def rework(self, task_id: str, actor: str, feedback: str, entry_tree: str, target: str | None = None, *, force_duplicate_start=False) -> TaskState:
        if not isinstance(entry_tree,str) or not entry_tree:
            raise DomainError("Для rework требуется наблюдаемое entry_tree")
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            execution, _ = uow.execution.load(task_id)
            if execution["pending"] is not None:
                raise DomainError("Неизвестен исход прерванной операции; rework запрещён")
            change=task.rework(actor,feedback,target)
            from .duplicate_tasks import require_duplicate_transition_in
            require_duplicate_transition_in(uow,task,change,actor,
                force_duplicate_start=force_duplicate_start)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{"entry_tree":entry_tree,"attempts":0,"publication":None,"pending":None})
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
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            execution, _ = uow.execution.load(task_id)
            handoff = uow.handoffs.latest_recovery_candidate(task_id)
            point = uow.tasks.empty_rework_recovery_point(
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
            uow.tasks.save(change, task.state.version)
            if transition_event == "user_accept_and_continue":
                uow.execution.patch(
                    task_id,
                    {"last_report": {**execution["last_report"], "status": "verified"}},
                )
            if (handoff is not None
                    and task.state.version >= handoff["version"] + 1
                    and handoff["receipt"]["verified"] is False
                    and handoff["receipt"]["stage"] == task.stage.stage_id
                    and handoff["receipt"]["iteration"] == task.state.iteration
                    and handoff["receipt"]["tree"] == tree):
                uow.handoffs.replace({**handoff, "state": "recovered"})
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
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            execution,_=uow.execution.load(task_id)
            if execution["pending"] is not None:
                raise DomainError("Неизвестен исход прерванной проверки; failed-check rework запрещён")
            change=task.rework_failed(actor,feedback,entry_tree,execution_key,target)
            from .duplicate_tasks import require_duplicate_transition_in
            require_duplicate_transition_in(uow,task,change,actor,
                force_duplicate_start=force_duplicate_start)
            uow.tasks.save(change,task.state.version)
            uow.execution.patch(task_id,{"entry_tree":entry_tree,"attempts":0,"publication":None,"pending":None})
            return change.task.state

    def inspection_registry_rework_available(self, task_id: str, target: str | None) -> bool:
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            return task.inspection_registry_blocker(target) is not None

    def failed_observation_batch(self, task_id, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return task.evidence_book.failed_batch(
                task.stage.stage_id,task.state.iteration,task.state.submission_digest,tree,execution_key)

    def cancel(self, task_id: str, actor: str, reason: str, cleanup: dict) -> TaskState:
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change=task.cancel(actor,reason)
            uow.tasks.save(change,task.state.version)
            execution,version=uow.execution.load(task_id)
            if execution['pending'] is not None:
                raise DomainError('External operation outcome must be resolved first')
            execution['pending']=cleanup
            uow.execution.save(task_id,execution,version)
            return change.task.state

    def evidence_precheck(self, task_id, actor, tree, execution_key):
        import json
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            task._owned(actor)
            if task.evidence_input is None:
                raise DomainError('Нет evidence submission')
            return task.evidence_book.precheck(task.evidence_plan,task.stage.stage_id,task.state.iteration,
                                               tree,execution_key,json.loads(task.evidence_input))

    def observation_batch(self, task_id, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return task.evidence_book.batch(task.stage.stage_id,task.state.iteration,tree,execution_key)

    def submission_observation_batch(
        self, task_id, submission_digest, tree, execution_key
    ):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            return task.evidence_book.submission_batch(
                task.stage.stage_id,task.state.iteration,
                submission_digest,tree,execution_key
            )

    def record_observations(self, task_id, actor, tree, execution_key, receipts):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            execution, _ = uow.execution.load(task_id)
            if is_check_attempt(execution['pending']):
                CheckAttempts.finish_in(uow, task, actor, tree, execution_key, receipts)
            else:
                if execution['pending'] is not None:
                    raise DomainError('Cannot finalize observations over another pending operation')
                change=task.record_observations(actor,tree,execution_key,receipts)
                uow.tasks.save(change,task.state.version)

    def begin_check_attempt(self, task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest):
        return CheckAttempts(self.unit_of_work).begin(task_id, actor, tree, execution_key, methods, limit, expected_version, submission_digest)

    def current_check_attempt(self, task_id, actor, tree, execution_key, methods):
        return CheckAttempts(self.unit_of_work).current(task_id, actor, tree, execution_key, methods)

    def start_check_run(self, task_id, actor, expected, run_id):
        return CheckAttempts(self.unit_of_work).start_run(task_id, actor, expected, run_id)

    def recover_pending_checks(
        self, task_id, actor, submission_digest, tree, execution_key, receipts
    ):
        with self.unit_of_work() as uow:
            task = uow.tasks.load(task_id)
            execution, execution_version = uow.execution.load(task_id)
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
            uow.tasks.save(change, task.state.version)
            uow.execution.save(
                task_id,
                {**execution, 'pending': None},
                execution_version,
            )

    def assess_evidence(self, task_id, actor, tree, execution_key):
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            change,assessment=task.assess_evidence(actor,tree,execution_key)
            uow.tasks.save(change,task.state.version)
            return assessment.to_dict()
