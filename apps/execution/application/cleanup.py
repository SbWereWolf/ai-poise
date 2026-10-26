"""One own transaction for cleanup decisions, observations and request receipts."""
import json

from apps.execution.application.gates import GateCommands
from apps.execution.domain.cleanup import CleanupPolicy, CleanupRun
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.domain.records import ExecutionRecord, RecordsError


class ExecutionCleanup:
    def __init__(self, unit_of_work, lifecycle, settings, state_owner, allocate_id):
        self.unit_of_work = unit_of_work
        self.lifecycle, self.settings = lifecycle, settings
        self.state, self.allocate_id = state_owner, allocate_id

    @staticmethod
    def _load(repository, task_id, cleanup_id):
        return repository.latest(task_id, 'cleanup_state', cleanup_id)

    def _authorize(self, execution, value):
        if execution['generation'] != value['expected_generation']:
            raise RecordsError('Cleanup generation conflict')
        if execution['contract_revision'] != value['expected_contract_revision']:
            raise RecordsError('Cleanup contract revision conflict')
        roles = CleanupPolicy.roles(self.settings, self.lifecycle)
        if LifecyclePolicy.actor(self.lifecycle, value['actor_id']) not in roles:
            raise RecordsError('Cleanup action requires a configured cleanup role')

    def _prepare(self, repository, execution, value, previous):
        if previous is not None:
            if previous.data['input'] != CleanupPolicy.semantic_prepare(value):
                raise RecordsError('Cleanup identity has a different immutable intent')
            return CleanupRun(previous.data)
        record = repository.outcome(value['outcome_id'])
        fact = record.data['fact'] if record is not None else None
        if fact is None or (fact['task_id'], fact['generation'], fact['contract_revision']) != (
                value['task_id'], value['expected_generation'], value['expected_contract_revision']):
            raise RecordsError('Cleanup requires the addressed terminal outcome of this own Task and generation')
        CleanupPolicy.resources(value['resources'], execution['workspace_id'])
        return CleanupRun.new(value, self.allocate_id)

    def _continue(self, previous, value):
        run = CleanupRun(previous.data)
        run.resume(value, self.allocate_id)
        return run

    def _observe(self, previous, value):
        run = CleanupRun(previous.data)
        run.observe(value['step_id'], value['attempt_id'], value['observation'], self.allocate_id)
        return run

    @staticmethod
    def _save(repository, value, run, previous):
        if previous is None or previous.data != run.value:
            repository.insert(ExecutionRecord('cleanup_state', value['cleanup_id'], value['task_id'],
                value['expected_generation'], 1 if previous is None else previous.revision + 1, run.value))

    def execute(self, value):
        digest = GateCommands.request_digest('cleanup', value)
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(value['request_id'])
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError('Cleanup request has different input; use its original request or a new ID')
                return dict(json.loads(prior['response_json']), replayed=True)
            revision = repository.get_revision()
            if revision != value['expected_revision']:
                raise RecordsError('Cleanup revision conflict; read the current revision')
            execution = self.state.execution(repository, value['task_id'])
            self._authorize(execution, value)
            previous = self._load(repository, value['task_id'], value['cleanup_id'])
            if value['action'] == 'prepare':
                run = self._prepare(repository, execution, value, previous)
            else:
                if previous is None:
                    raise RecordsError('Select an existing own cleanup obligation')
                if (previous.generation != value['expected_generation']
                        or previous.data['input']['expected_contract_revision'] != value['expected_contract_revision']):
                    raise RecordsError('Cleanup obligation belongs to its original generation and contract')
                run = (self._continue(previous, value) if value['action'] == 'continue'
                       else self._observe(previous, value))
            self._save(repository, value, run, previous)
            response = {'schema': 'stride-execution/v1', 'status': 'cleanup_recorded',
                        'revision': revision + 1, 'replayed': False, 'cleanup': run.result()}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    def query(self, repository, task_id, cleanup_id):
        self.state.execution(repository, task_id)
        previous = self._load(repository, task_id, cleanup_id)
        if previous is None:
            raise RecordsError('Select an existing addressed cleanup obligation for this Task')
        return CleanupRun(previous.data).result()
