"""Receive facts within the existing application-owned apply transaction."""
from apps.sequencing.domain.execution_facts import (
    label_fact, require_outcome_target, require_same_fact, state_for_outcome, terminal_for_outcome,
)
from apps.sequencing.domain.ownership import require_source


class ExecutionFactsCommands:
    def __init__(self, repository, now):
        self.repository = repository
        self.now = now

    @staticmethod
    def _replay(saved, fact, kind):
        if saved is None:
            return None
        require_same_fact(saved['fact'], fact, kind)
        return {**saved['result'], 'changed': False, 'fact_replayed': True}

    def execute(self, operation):
        if operation['kind'] == 'record_stage_label':
            return self.record_stage_label(operation)
        return self.record_execution_outcome(operation)

    def record_stage_label(self, operation):
        fact = label_fact(operation)
        replay = self._replay(self.repository.label(fact['label_id']), fact, 'label')
        if replay is not None:
            return replay
        self.repository.ownership.task(fact['task_id'])
        require_source(fact['task_id'], operation['claim_id'], operation['claimant'],
                       self.repository.ownership.active_for_task(fact['task_id']))
        result = {'changed': True, 'task_id': fact['task_id'], 'label_id': fact['label_id'],
                  'fact_replayed': False}
        self.repository.record_label(fact, result, operation['claimant'], self.now())
        return result

    def record_execution_outcome(self, operation):
        fact = operation['outcome']
        replay = self._replay(self.repository.outcome(fact['outcome_id']), fact, 'outcome')
        if replay is not None:
            return replay
        task = self.repository.ownership.task(fact['task_id'])
        require_outcome_target(task['state'],
                               self.repository.ownership.active_for_task(fact['task_id']),
                               self.repository.workspace.saved(fact['task_id']))
        state = state_for_outcome(fact['kind'])
        self.repository.set_outcome(fact['task_id'], state, terminal_for_outcome(fact))
        result = {'changed': True, 'task_id': fact['task_id'], 'outcome_id': fact['outcome_id'],
                  'state': state, 'fact_replayed': False}
        self.repository.record_outcome(fact, result, fact['source_ref']['owner'], self.now())
        return result
