"""Addressed execution outcomes within the existing own record transaction."""
from copy import deepcopy
import json

from apps.execution.application.gates import GateCommands
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.domain.outcomes import OUTCOME_RECORD_KIND, OutcomePolicy
from apps.execution.domain.verification import VerificationPolicy
from apps.execution.domain.records import RecordsError


class ExecutionOutcomes:
    def __init__(self, unit_of_work, lifecycle_settings, outcome_settings, state_owner, evidence):
        self.unit_of_work = unit_of_work
        self.lifecycle, self.settings = lifecycle_settings, outcome_settings
        self.state, self.evidence = state_owner, evidence

    def _terminal_proof(self, repository, execution, state, records, scope, source):
        process = self.state.process(repository, execution)
        terminal = OutcomePolicy.terminal(records, scope, execution, process)
        reference = terminal['traversal']['current_evidence_id']
        evidence = repository.latest(execution['task_id'], 'evidence', reference)
        attempt = (repository.latest(execution['task_id'], 'attempt', evidence.data['attempt_id'])
                   if evidence is not None else None)
        if attempt is None:
            raise RecordsError('Select the admitted terminal evidence and its actual attempt')
        proof = self.evidence.proof(repository, execution, process, execution['current_stage'],
            execution['iteration'], {'evidence_id': reference,
                'context_id': attempt.data['context_id'], 'candidate_id': attempt.data['candidate_id']})
        if proof['gate_id'] != state['presented_result']:
            raise RecordsError('Terminal proof must match the actually presented current Gate')
        if source != proof['attempt']['source'] or source != terminal['visit']['source']:
            raise RecordsError('Outcome source must match the admitted original and current terminal proof')
        terminal['current_gate_id'] = proof['gate_id']
        return process, terminal

    def execute(self, value):
        digest = GateCommands.request_digest('outcome', value)
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(value['request_id'])
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError('Outcome request has different input; replay its original request or use a new request ID')
                return dict(json.loads(prior['response_json']), replayed=True)
            revision = repository.get_revision()
            if revision != value['expected_revision']:
                raise RecordsError('Outcome revision conflict; read the current revision')
            addressed = repository.outcome(value['outcome_id'])
            semantic = OutcomePolicy.semantic_input(value)
            if addressed is not None:
                if addressed.task_id != value['task_id'] or addressed.data['input'] != semantic:
                    raise RecordsError('Outcome identity already names a different Task or decision; use the original fact or a new outcome ID')
                fact, changed = deepcopy(addressed.data['fact']), False
            else:
                execution = self.state.execution(repository, value['task_id'])
                if execution['generation'] != value['expected_generation']:
                    raise RecordsError('Outcome generation conflict')
                if execution['contract_revision'] != value['expected_contract_revision']:
                    raise RecordsError('Outcome contract revision conflict')
                role = LifecyclePolicy.actor(self.lifecycle, value['actor_id'])
                if role not in OutcomePolicy.roles(self.settings, self.lifecycle):
                    raise RecordsError('Outcome decision requires a configured accepting role')
                records = repository.list_records(value['task_id'])
                scope = OutcomePolicy.work_scope(records, execution['generation'])
                VerificationPolicy.require_resolved(records, execution)
                OutcomePolicy.require_open(records, scope)
                state = self.state.state(repository, execution)
                process, terminal = (self._terminal_proof(repository, execution, state,
                    records, scope, value['source']) if value['intent'] == 'accept' else (None, None))
                fact = OutcomePolicy.fact(execution, state['requirements_snapshot'], value,
                                          role, process, terminal)
                self.state.put(repository, execution, OUTCOME_RECORD_KIND, value['outcome_id'],
                    {'fact': fact, 'scope': scope, 'input': semantic})
                changed = True
            response = {'schema': 'stride-execution/v1', 'status': 'outcome_recorded',
                'revision': revision + 1, 'replayed': False, 'changed': changed, 'fact': fact}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    def query(self, repository, task_id, outcome_id):
        self.state.execution(repository, task_id)
        record = repository.outcome(outcome_id)
        if record is None or record.task_id != task_id:
            raise RecordsError('Unknown addressed outcome for this Task; select its actual outcome_id')
        return deepcopy(record.data['fact'])
