"""Application API for observed command evidence. I/O remains behind ports."""
import json

from ..modules.evidence.domain import EvidenceBook
from ..modules.foundation.errors import DomainError
from ..modules.tasks.ports import TaskReadSet


def check_receipts_by_id(records):
    """Select command receipts without interpreting opaque historical evidence."""
    return {receipt['id']: receipt for receipt in records
            if isinstance(receipt, dict) and isinstance(receipt.get('id'), str)}


class EvidenceCommands:
    def __init__(self, unit_of_work):
        self.unit_of_work=unit_of_work

    def record_receipt(self, task_id, actor, stage, iteration, receipt):
        # Validation is a pure library operation, not a renderer's responsibility.
        EvidenceBook.empty().record_batch(stage,iteration,receipt['tree'],receipt['id'],[receipt])
        facade = self.unit_of_work()
        inputs = facade.capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        if task.state.claimed_by != actor or task.state.status != 'active':
            raise DomainError('Receipt requires current active task owner')
        if (task.stage.stage_id,task.state.iteration)!=(stage,iteration):
            raise DomainError('Receipt belongs to a different stage/iteration')
        admitted = (task.check_registry.method_ids if task.replay_candidate is None else
                    json.loads(task.replay_candidate)['method_ids'])
        if set(receipt['obligations'])-set(admitted):
            raise DomainError('Receipt references unknown verification method')
        encoded = facade.prepare_evidence(receipt)
        with self.unit_of_work() as uow:
            uow.require_inputs(inputs)
            uow.evidence.record_prepared(task_id, stage, iteration, receipt['id'], encoded)

    def list_for(self,task_id):
        inputs = self.unit_of_work().capture(TaskReadSet((), evidence_ids=(task_id,)))
        return inputs.evidence(task_id)

    def timeout_history(self, profile):
        return self.unit_of_work().timeout_history(profile)
