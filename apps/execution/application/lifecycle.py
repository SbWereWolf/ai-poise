"""Own agreement/revision/restart/rework transactions; no physical workspace effects."""
from copy import deepcopy
import hashlib
import json

from apps.execution.application.gates import GateCommands, internal_record_id
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.domain.verification import VerificationPolicy
from apps.execution.domain.records import RecordsError, canonical_json
from apps.execution.application.execution_state import ExecutionState


class ExecutionLifecycle:
    def __init__(self, unit_of_work, settings, definitions, records, validate_contract, state_owner):
        self.state_owner = state_owner
        self.unit_of_work = unit_of_work
        self.settings = settings
        self.definitions = definitions
        self.records = records
        self.validate_contract = validate_contract

    def _policy(self, contract):
        if self.settings is None or contract['restart_policy_id'] not in self.settings['policies']:
            raise RecordsError('Configure the selected lifecycle restart_policy_id')
        return self.settings['policies'][contract['restart_policy_id']]

    @staticmethod
    def _proposal_id(execution, proposal_id):
        return f'restart-proposal:{execution["generation"]}:{proposal_id}'

    def _proposal(self, repository, execution, proposal_id):
        record = repository.latest(execution['task_id'], 'restart_proposal',
                                   self._proposal_id(execution, proposal_id))
        if record is None:
            raise RecordsError('Select an existing own current-generation proposal')
        return record.data

    def execute(self, value):
        digest = GateCommands.request_digest('lifecycle', value)
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(value['request_id'])
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError('Lifecycle request has different input; use the original request or a new id')
                return dict(json.loads(prior['response_json']), replayed=True)
            revision = repository.get_revision()
            if revision != value['expected_revision']:
                raise RecordsError('Lifecycle revision conflict; read the current revision')
            execution = self.state_owner.execution(repository, value['task_id'])
            if execution['generation'] != value['expected_generation']:
                raise RecordsError('Lifecycle generation conflict')
            if execution['contract_revision'] != value['expected_contract_revision']:
                raise RecordsError('Lifecycle contract revision conflict')
            if value['action'] in ('enter', 'restart', 'rework'):
                VerificationPolicy.require_resolved(repository.list_records(value['task_id']), execution)
            role = LifecyclePolicy.actor(self.settings, value['actor_id'])
            policy = self._policy(execution['contract'])
            state = self.state_owner.state(repository, execution)
            before = deepcopy(execution)
            details = getattr(self, '_' + value['action'])(repository, execution, state, value, role, policy)
            # Canonical state is written before a new-generation record. The
            # enclosing UoW rolls back this prior UPDATE if later INSERT fails.
            if execution != before:
                repository.save_execution_state(execution, before)
            if value['action'] == 'restart':
                self.records.setup_snapshot(repository, execution['contract'],
                                            execution['contract_revision'], execution['generation'])
                self.records.setup_process_snapshot(repository, execution['contract'],
                                                    details.pop('_process'), execution['generation'])
            self.state_owner.save_state(repository, execution, state)
            record_id = 'lifecycle-event:' + value['request_id']
            event = {'action': value['action'], 'actor_id': value['actor_id'], 'role_id': role,
                     'request_id': value['request_id'], 'reason': value.get('reason'),
                     'before_generation': before['generation'], 'after_generation': execution['generation'],
                     **details}
            if value['action'] == 'restart':
                event.update(before_contract=before['contract'], after_contract=execution['contract'])
            self.state_owner.put(repository, execution, 'lifecycle_event', record_id, event)
            response = {'schema': 'stride-execution/v1', 'status': 'lifecycle_applied',
                        'revision': revision + 1, 'replayed': False,
                        **{key: execution[key] for key in ('task_id', 'generation', 'contract_revision',
                                                         'state', 'current_stage', 'iteration')},
                        'result': {'action': value['action'], 'id': record_id, **details}}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    def _freeze(self, repository, execution, state, value, role, policy):
        LifecyclePolicy.require_state(execution, ('draft',))
        snapshot = value['requirements_snapshot']
        goal = LifecyclePolicy.validate_snapshot(snapshot, execution['contract'], state['registered_goal'])
        if state['requirements_snapshot'] is not None and snapshot != state['requirements_snapshot']:
            raise RecordsError('Freeze must preserve the exact agreed Requirements snapshot')
        LifecyclePolicy.validate_process(self.state_owner.process(repository, execution), execution['contract'], RecordsError)
        execution.update(state='ready', current_stage=None, iteration=1)
        state.update(registered_goal=goal, requirements_snapshot=deepcopy(snapshot))
        if state['performer_actor_id'] is None:
            state['performer_actor_id'] = value['actor_id']
        return {}

    def _enter(self, repository, execution, state, value, role, policy):
        LifecyclePolicy.require_state(execution, ('ready',))
        entry = LifecyclePolicy.entry(self.state_owner.process(repository, execution), role)
        execution.update(state='active', current_stage=entry, iteration=1)
        state.update(entry_tree=value['entry_tree'], visits={entry: 1},
                     performer_actor_id=value['actor_id'], presented_result=None)
        return {'entry_tree': value['entry_tree']}

    def _present(self, repository, execution, state, value, role, policy):
        LifecyclePolicy.require_state(execution, ('active',))
        process = self.state_owner.process(repository, execution)
        stage = next(stage for stage in process['stages'] if stage['id'] == execution['current_stage'])
        if stage['role'] != role:
            raise RecordsError('Presented stage role must match the configured actor role')
        result_id = internal_record_id('stage_result', execution['generation'], value['context_id'], value['candidate_id'])
        result = repository.latest(execution['task_id'], 'stage_result', result_id)
        LifecyclePolicy.presented(execution, result, execution['contract'])
        execution['state'] = 'verified'
        state.update(presented_result=result_id, performer_actor_id=value['actor_id'])
        return {'context_id': value['context_id'], 'candidate_id': value['candidate_id'], 'presented_result': result_id}

    def _propose_restart(self, repository, execution, state, value, role, policy):
        LifecyclePolicy.require_state(execution, ('ready', 'active', 'verified'))
        desired = value['contract']
        self.validate_contract(desired)
        changed = LifecyclePolicy.revision(execution['contract'], desired, policy['revision_fields'])
        LifecyclePolicy.validate_snapshot(value['requirements_snapshot'], desired, state['registered_goal'])
        if desired['process_ref'] == execution['contract']['process_ref']:
            process = self.state_owner.process(repository, execution)
        else:
            if self.definitions is None:
                raise RecordsError('Configure process definitions for a new selected process')
            process = self.definitions.resolve(desired['process_ref'])
        LifecyclePolicy.validate_process(process, desired, RecordsError)
        proposal = {'proposal_id': value['proposal_id'], 'before_contract': execution['contract'],
                    'before_generation': execution['generation'],
                    'before_snapshot': state['requirements_snapshot'], 'contract': desired,
                    'requirements_snapshot': value['requirements_snapshot'], 'process': process,
                    'changed_fields': changed, 'performer_actor_id': state['performer_actor_id'],
                    'proposer_actor_id': value['actor_id'], 'reason': value['reason']}
        self.state_owner.put(repository, execution, 'restart_proposal', self._proposal_id(execution, value['proposal_id']), proposal)
        return {'proposal_id': value['proposal_id']}

    def _decide_restart(self, repository, execution, state, value, role, policy):
        proposal = self._proposal(repository, execution, value['proposal_id'])
        LifecyclePolicy.approval(proposal, value['actor_id'], role, policy)
        LifecyclePolicy.revision(proposal['before_contract'], proposal['contract'], policy['revision_fields'])
        approval_id = f'restart-approval:{execution["generation"]}:{value["request_id"]}'
        decision = {'approval_id': approval_id, 'proposal_id': value['proposal_id'],
                    'actor_id': value['actor_id'], 'role_id': role, 'decision': value['decision'],
                    'reason': value['reason'], 'authorized_fields': policy['revision_fields'],
                    'before_generation': execution['generation']}
        self.state_owner.put(repository, execution, 'restart_approval', approval_id, decision)
        return {'proposal_id': value['proposal_id'], 'approval_id': approval_id, 'decision': value['decision']}

    def _restart(self, repository, execution, state, value, role, policy):
        LifecyclePolicy.require_state(execution, ('ready', 'active', 'verified'))
        proposal = self._proposal(repository, execution, value['proposal_id'])
        decision = repository.latest(execution['task_id'], 'restart_approval', value['approval_id'])
        if (decision is None or decision.generation != execution['generation']
                or decision.data['proposal_id'] != value['proposal_id']
                or decision.data['decision'] != 'approved'):
            raise RecordsError('Select an approved own proposal and approval')
        if proposal['before_generation'] != execution['generation'] or proposal['before_contract'] != execution['contract']:
            raise RecordsError('Restart proposal must match the current generation and contract')
        desired = proposal['contract']
        LifecyclePolicy.revision(execution['contract'], desired, decision.data['authorized_fields'])
        LifecyclePolicy.validate_snapshot(proposal['requirements_snapshot'], desired, state['registered_goal'])
        LifecyclePolicy.validate_process(proposal['process'], desired, RecordsError)
        execution.update(generation=execution['generation'] + 1, contract=deepcopy(desired),
                         contract_revision=hashlib.sha256(canonical_json(desired).encode()).hexdigest(),
                         state='draft', current_stage=None, iteration=1)
        state.update(requirements_snapshot=deepcopy(proposal['requirements_snapshot']), entry_tree=None,
                     visits={}, performer_actor_id=None, presented_result=None)
        return {'proposal_id': value['proposal_id'], 'approval_id': value['approval_id'],
                '_process': proposal['process']}

    def _rework(self, repository, execution, state, value, role, policy):
        target = value['target_stage_id']
        iteration = LifecyclePolicy.rework(execution, self.state_owner.process(repository, execution), state['visits'], target)
        if state['presented_result'] is None:
            raise RecordsError('Rework state requires an actually presented own result')
        execution.update(state='active', current_stage=target, iteration=iteration)
        state['visits'][target] = iteration
        state.update(entry_tree=value['entry_tree'], presented_result=None)
        return {'target_stage_id': target, 'entry_tree': value['entry_tree']}

    def query(self, repository, kind, task_id):
        execution = self.state_owner.execution(repository, task_id)
        history = [record.data for record in repository.list_records(task_id) if record.kind == 'lifecycle_event']
        if kind == 'restart_history':
            return [item for item in history if item['action'] in ('propose_restart', 'decide_restart', 'restart')]
        state = self.state_owner.state(repository, execution)
        return {'registered_goal': state['registered_goal'],
                'requirements_snapshot': state['requirements_snapshot'],
                'execution': self.state_owner.view(execution), 'entry_tree': state['entry_tree'], 'history': history}
