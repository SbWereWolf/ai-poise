"""Application API for observed command evidence. I/O remains behind ports."""
import json

from ..modules.evidence.domain import EvidenceBook
from ..modules.foundation.errors import DomainError


class EvidenceCommands:
    def __init__(self, unit_of_work):
        self.unit_of_work=unit_of_work

    def record_receipt(self, task_id, actor, stage, iteration, receipt):
        # Validation is a pure library operation, not a renderer's responsibility.
        EvidenceBook.empty().record_batch(stage,iteration,receipt['tree'],receipt['id'],[receipt])
        with self.unit_of_work() as uow:
            task=uow.tasks.load(task_id)
            if task.state.claimed_by != actor or task.state.status != 'active':
                raise DomainError('Receipt requires current active task owner')
            if (task.stage.stage_id,task.state.iteration)!=(stage,iteration):
                raise DomainError('Receipt belongs to a different stage/iteration')
            admitted = (task.check_registry.method_ids if task.replay_candidate is None else
                        json.loads(task.replay_candidate)['method_ids'])
            if set(receipt['obligations'])-set(admitted):
                raise DomainError('Receipt references unknown verification method')
            uow.evidence.record(task_id,stage,iteration,receipt)

    def list_for(self,task_id):
        with self.unit_of_work() as uow:
            return uow.evidence.list_for(task_id)

    def timeout_history(self, profile):
        with self.unit_of_work() as uow:
            return uow.evidence.timeout_history(profile)
