"""Task creation compilation; external preflight sources are retained by callers."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedCreationWrite:
    allocation: object
    receipt: object | None
    contract: dict
    rows: object | None
    newborn: object | None
    newborn_metadata: str | None
    requirements_patch: object | None

    def save(self, uow):
        if self.allocation.replayed:
            if self.requirements_patch is not None:
                uow.tasks.patch_prepared_requirements(self.requirements_patch)
            return
        if self.newborn is None:
            uow.tasks.create_prepared(self.rows)
        else:
            uow.tasks.create_newborn_prepared(self.newborn, self.newborn_metadata)
            uow.tasks.promote_prepared(self.rows, self.newborn.version)


def compile_creation(facade, prepared, allocation, base_metadata, receipt, *, actor):
    from .tasks import _creation_values, PreparedCreation
    from ..modules.foundation.errors import DomainError
    if not isinstance(prepared, PreparedCreation):
        raise DomainError('Task creation requires a verified creation preflight')
    from ..modules.tasks.newborn import NewbornTask
    task, metadata, contract = _creation_values(prepared.intent, allocation, None,
        prepared.workflow, prepared.automatic_checks, prepared.decomposition_policy,
        base_metadata, prepared.requirements_context)
    if allocation.replayed:
        if receipt is None:
            raise DomainError('Replayed creation has no captured receipt; repeat the request')
        previous = receipt.requirements_context()
        updated = prepared.requirements_gate.prepare_created_metadata(
            previous, prepared.requirements_context)
        patch = facade.prepare_requirements_patch(receipt, updated)
        return PreparedCreationWrite(allocation, receipt, contract, None, None, None, patch)
    metadata = prepared.requirements_gate.prepare_created_metadata(metadata, prepared.requirements_context)
    if actor is None:
        return PreparedCreationWrite(allocation, None, contract,
            facade.prepare_task_creation(task, metadata), None, None, None)
    newborn = NewbornTask.create(allocation.task_id, None, actor)
    source = facade.new_promotion_source(newborn, base_metadata['config_hash'])
    return PreparedCreationWrite(allocation, None, contract,
        facade.prepare_promotion(source, task, metadata), newborn,
        facade.prepare_newborn_create(newborn, base_metadata['config_hash']), None)


def compile_creation_batch(facade, preparations, namespace, base_metadata, policy, reservations):
    from ..modules.tasks.allocation import TaskIdPolicy, creation_parts
    from ..modules.foundation.errors import DomainError
    used = set()
    writes = []
    for prepared in preparations:
        request_id, _, _ = creation_parts(prepared.source_intent)
        parsed = None if request_id is None else TaskIdPolicy.parse(policy)
        allocation = namespace.allocate(prepared.source_intent, parsed, (*reservations, *used))
        if allocation.task_id in used:
            raise DomainError('Child identity collides with existing work')
        used.add(allocation.task_id)
        receipt = None if request_id is None else namespace.creation_receipt(request_id)
        writes.append(compile_creation(facade, prepared, allocation, base_metadata, receipt, actor=None))
    return tuple(writes)
