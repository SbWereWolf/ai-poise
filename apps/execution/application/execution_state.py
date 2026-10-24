"""Canonical own execution, lifecycle-state and frozen-process record access."""
from copy import deepcopy

from apps.execution.domain.records import ExecutionRecord, RecordsError


class ExecutionState:
    @staticmethod
    def view(execution):
        return {key: execution[key] for key in ('task_id', 'generation', 'contract_revision',
                'state', 'current_stage', 'iteration', 'workspace_id', 'admission_id')}

    @staticmethod
    def execution(repository, task_id):
        value = repository.execution_state(task_id)
        if value is None:
            raise RecordsError('Select an existing canonical execution task_id')
        return value

    @staticmethod
    def state_id(execution):
        return f'lifecycle-state:{execution["task_id"]}:{execution["generation"]}'

    def state(self, repository, execution):
        previous = repository.latest(execution['task_id'], 'lifecycle_state', self.state_id(execution))
        return (deepcopy(previous.data) if previous else {
            'registered_goal': None, 'requirements_snapshot': None, 'entry_tree': None,
            'visits': {}, 'performer_actor_id': None, 'presented_result': None})

    def save_state(self, repository, execution, state):
        record_id = self.state_id(execution)
        previous = repository.latest(execution['task_id'], 'lifecycle_state', record_id)
        if previous is None or previous.data != state:
            repository.insert(ExecutionRecord('lifecycle_state', record_id,
                execution['task_id'], execution['generation'], 1 if previous is None else previous.revision + 1,
                deepcopy(state)))

    @staticmethod
    def put(repository, execution, kind, record_id, value):
        if repository.latest(execution['task_id'], kind, record_id) is not None:
            raise RecordsError(f'Preserve existing own {kind} identity')
        repository.insert(ExecutionRecord(kind, record_id, execution['task_id'],
                                          execution['generation'], 1, deepcopy(value)))

    @staticmethod
    def process(repository, execution):
        snapshot = repository.latest(execution['task_id'], 'process_snapshot',
                                     f'process:{execution["task_id"]}:{execution["generation"]}')
        if snapshot is None or snapshot.data['process_ref'] != execution['contract']['process_ref']:
            raise RecordsError('Select an execution with its own frozen process snapshot')
        return snapshot.data['process']

