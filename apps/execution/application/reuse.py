"""Reuse own accepted facts with fresh local verification; callers own delivery."""
from copy import deepcopy
import json

from apps.execution.application.gates import GateCommands, internal_record_id
from apps.execution.domain.outcomes import OUTCOME_RECORD_KIND, OutcomePolicy
from apps.execution.domain.progression import ProgressionPolicy
from apps.execution.domain.records import ExecutionRecord, RecordsError
from apps.execution.domain.reuse import REUSE_KIND, ReusePolicy
from apps.execution.domain.verification import VerificationPolicy


class ExecutionReuse:
    def __init__(self, unit_of_work, config, state, verification, gates):
        self.unit_of_work, self.config, self.state = unit_of_work, config, state
        self.verification, self.gates = verification, gates

    @staticmethod
    def save(repository, data):
        value = data['value']
        prior = repository.latest(value['task_id'], REUSE_KIND, value['reuse_id'])
        repository.insert(ExecutionRecord(REUSE_KIND, value['reuse_id'], value['task_id'],
            value['generation'], 1 if prior is None else prior.revision + 1, deepcopy(data)))

    def query(self, repository, task_id, reuse_id):
        self.state.execution(repository, task_id)
        record = repository.latest(task_id, REUSE_KIND, reuse_id)
        if record is None:
            raise RecordsError('Select an existing own reuse_id')
        return deepcopy(record.data['value'])

    def load(self, repository, execution, reuse_id):
        record = repository.latest(execution['task_id'], REUSE_KIND, reuse_id)
        if record is None:
            raise RecordsError('Select an existing own reuse_id')
        data = deepcopy(record.data)
        value = data['value']
        if (value['generation'] != execution['generation']
                or value['contract_revision'] != execution['contract_revision']
                or data['scope'] != OutcomePolicy.work_scope(repository.list_records(execution['task_id']), execution['generation'])):
            raise RecordsError('Reuse candidate belongs to earlier current work; prepare a current candidate')
        return data

    def source(self, repository, task_id, outcome_id):
        record = repository.outcome(outcome_id)
        if record is None or record.task_id != task_id:
            raise RecordsError('Select the actual accepted source outcome_id')
        fact = deepcopy(record.data['fact'])
        generation = fact['generation']
        contract = repository.latest(task_id, 'contract_snapshot', f'contract:{task_id}:{generation}')
        process = repository.latest(task_id, 'process_snapshot', f'process:{task_id}:{generation}')
        state = repository.latest(task_id, 'lifecycle_state', f'lifecycle-state:{task_id}:{generation}')
        if (contract is None or process is None or state is None
                or contract.data['contract_revision'] != fact['contract_revision']):
            raise RecordsError('Accepted source requires its actual frozen generation and contract')
        return fact, contract.data['contract'], process.data['process'], state.data['requirements_snapshot']

    def admit(self, repository, execution, value, candidate):
        fact, contract, process, snapshot = self.source(repository,
            candidate['source_task_id'], candidate['source_outcome_id'])
        ReusePolicy.admit(value['admission'], fact, execution['task_id'])
        ReusePolicy.compatible(contract, process, snapshot, execution['contract'],
            self.state.process(repository, execution), self.state.state(repository, execution)['requirements_snapshot'])
        runner = self.verification.runner()
        if runner.source() != candidate['source']:
            raise RecordsError('Reuse source must match the current verified local source')
        runner.require_ancestor(fact['result_commit'], runner.settings['base_ref'])
        runner.require_ancestor(fact['result_commit'], 'HEAD')
        _, gate = self.verification.candidate(repository, execution,
            candidate['context_id'], candidate['candidate_id'])
        source_gate = repository.latest(fact['task_id'], 'stage_result',
                                        fact['provenance']['current_gate_id'])
        # Gate owns scoped file meaning. Reobserve delivered files; no implicit copy.
        for mapping in candidate['artifact_mapping']:
            for side in ('source', 'target'):
                scope, relative = mapping[side + '_scope'], mapping[side + '_path']
                admitted_gate = source_gate if side == 'source' else gate
                if (admitted_gate is None or {'scope': scope, 'relative_path': relative}
                        not in admitted_gate.data['files']):
                    raise RecordsError('Reuse mapping must name files in the accepted source and current target Gate')
                if self.gates.files is None:
                    raise RecordsError('Configure Gate file roots for reuse artifact mapping')
                _, observed, _, errors = self.gates.files.observe([], [
                    str(self.gates.files.roots[scope] / relative)])
                if errors or {'scope': scope, 'relative_path': relative} not in observed:
                    raise RecordsError('Reuse artifact mapping requires the delivered scoped files')
        if gate.data['files']:
            _, observed, _, errors = self.gates.files.observe(gate.data['files'], [])
            if errors or observed != gate.data['files']:
                raise RecordsError('Reuse requires all current Gate result files to remain delivered')
        return fact, gate

    @staticmethod
    def response(repository, value, data, status, *, save=True, changed=None):
        revision = repository.get_revision()
        result = {'schema': 'stride-execution/v1', 'status': status,
                  'revision': revision + int(save), 'replayed': False,
                  'changed': save if changed is None else changed, 'reuse': deepcopy(data['value'])}
        if save:
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], GateCommands.request_digest('reuse', value), result)
        return result

    def execute(self, value):
        self.verification.settings()
        if value['action'] == 'verify':
            return self.verify(value)
        with self.unit_of_work(write=True) as repository:
            prior = self.verification.prior(repository, value, 'reuse')
            if prior is not None:
                return prior
            execution, role = self.verification.binding(repository, value)
            records = repository.list_records(value['task_id'])
            scope = OutcomePolicy.work_scope(records, execution['generation'])
            # An addressed historical decision remains readable after genuine new work.
            if value['action'] == 'accept':
                prior_fact = repository.outcome(value['outcome_id'])
                if prior_fact is not None:
                    old = prior_fact.data
                    if prior_fact.task_id != value['task_id'] or old.get('input') != OutcomePolicy.semantic_input(value):
                        raise RecordsError('Reuse outcome identity already names a different decision')
                    data = deepcopy(repository.latest(value['task_id'], REUSE_KIND, value['reuse_id']).data)
                    return self.response(repository, value, data, 'reuse_accepted', changed=False)
            OutcomePolicy.require_open(records, scope)
            VerificationPolicy.require_resolved(records, execution)
            if value['action'] == 'prepare':
                previous = repository.latest(value['task_id'], REUSE_KIND, value['reuse_id'])
                if previous is not None:
                    data = self.load(repository, execution, value['reuse_id'])
                    identity_fields = ('source_task_id', 'source_outcome_id', 'source',
                                       'context_id', 'candidate_id', 'artifact_mapping')
                    if any(data['value'][key] != value[key] for key in identity_fields):
                        raise RecordsError('Reuse identity conflicts with its original candidate')
                    self.admit(repository, execution, value, data['value'])
                    return self.response(repository, value, data, 'reuse_prepared', changed=False)
                candidate = {key: deepcopy(value[key]) for key in (
                    'reuse_id', 'task_id', 'source_task_id', 'source_outcome_id', 'source',
                    'context_id', 'candidate_id', 'artifact_mapping')}
                candidate.update(generation=execution['generation'], contract_revision=execution['contract_revision'],
                    state='prepared', attempt_id=None, evidence_id=None, fact=None)
                data = {'value': candidate, 'scope': scope}
                self.admit(repository, execution, value, candidate)
                self.save(repository, data)
                return self.response(repository, value, data, 'reuse_prepared')
            data = self.load(repository, execution, value['reuse_id'])
            candidate = data['value']
            fact, gate = self.admit(repository, execution, value, candidate)
            if candidate['state'] != 'verified':
                raise RecordsError('Reuse acceptance requires current successful local verification')
            attempt = self.verification.load(repository, execution, candidate['attempt_id'])
            if not self.assess(attempt, execution):
                raise RecordsError('Reuse acceptance requires successful current local receipts')
            if role not in OutcomePolicy.roles(self.config.get('outcomes'), self.config.get('lifecycle')):
                raise RecordsError('Reuse acceptance requires a configured accepting role')
            accepted = ReusePolicy.fact(execution, self.state.state(repository, execution)['requirements_snapshot'],
                fact, candidate, gate.record_id, value, role)
            self.state.put(repository, execution, OUTCOME_RECORD_KIND, value['outcome_id'],
                {'fact': accepted, 'scope': scope, 'input': OutcomePolicy.semantic_input(value)})
            candidate.update(state='accepted', fact=accepted)
            self.save(repository, data)
            return self.response(repository, value, data, 'reuse_accepted')

    @staticmethod
    def assess(attempt, execution):
        generic = VerificationPolicy.generic_attempt(attempt)
        rules = execution['contract']['evidence_plan'][attempt['value']['stage_id']]['subject_methods']
        reason = ProgressionPolicy.admitted_observations(attempt['methods'], generic,
            attempt['value']['source'], rules)
        return reason is None and all(run['receipt']['passed'] for run in attempt['value']['runs'])

    def verify(self, value):
        changed = False
        with self.unit_of_work(write=True) as repository:
            prior = self.verification.prior(repository, value, 'reuse')
            if prior is not None:
                return prior
            execution, _ = self.verification.binding(repository, value)
            records = repository.list_records(value['task_id'])
            OutcomePolicy.require_open(records, OutcomePolicy.work_scope(records, execution['generation']))
            data = self.load(repository, execution, value['reuse_id'])
            candidate = data['value']
            self.admit(repository, execution, value, candidate)
            if candidate['state'] == 'accepted':
                raise RecordsError('Reuse already has an accepted final decision')
            if candidate['state'] == 'verifying':
                if candidate['attempt_id'] != value['attempt_id']:
                    raise RecordsError('Reuse attempt is pending; continue the same attempt_id')
                attempt = self.verification.load(repository, execution, value['attempt_id'])
            else:
                VerificationPolicy.require_resolved(records, execution)
                methods = ReusePolicy.produced_methods(execution['contract'])
                begin = {**value, 'context_id': candidate['context_id'], 'candidate_id': candidate['candidate_id'],
                         'source': candidate['source'], 'method_ids': methods}
                attempt = self.verification.begin_in(repository, execution, begin,
                    purpose='reuse', owner_id=value['reuse_id'], method_ids=methods)
                candidate.update(state='verifying', attempt_id=value['attempt_id'], evidence_id=None)
                self.save(repository, data)
                repository.set_revision(repository.get_revision() + 1)
                changed = True
            VerificationPolicy.require_actor(attempt, value['actor_id'])
        for run in attempt['value']['runs']:
            if run['receipt'] is not None:
                continue
            with self.unit_of_work(write=False) as repository:
                revision = repository.get_revision()
            action = 'recover' if run['started'] else 'run'
            packet = {key: value[key] for key in ('task_id', 'expected_generation', 'expected_contract_revision', 'actor_id')}
            packet.update(action=action, expected_revision=revision, attempt_id=value['attempt_id'],
                run_id=run['run_id'], request_id=value['request_id'] + ':' + action + ':' + run['run_id'])
            result = self.verification.execute(packet)
            changed = changed or result['changed']
            if result['status'] == 'pending':
                with self.unit_of_work(write=False) as repository:
                    return self.response(repository, value, data, 'pending', save=False, changed=changed)
        with self.unit_of_work(write=True) as repository:
            execution = self.state.execution(repository, value['task_id'])
            data = self.load(repository, execution, value['reuse_id'])
            self.admit(repository, execution, value, data['value'])
            attempt = self.verification.load(repository, execution, value['attempt_id'])
            if attempt['value']['state'] != 'finished':
                self.verification.finish_in(repository, execution, attempt,
                    {'phase': 'prepare', 'arguments': [], 'decisions': []})
            passed = self.assess(attempt, execution)
            data['value'].update(state='verified' if passed else 'checks_failed',
                                 evidence_id=attempt['value']['evidence_id'])
            self.save(repository, data)
            return self.response(repository, value, data, 'reuse_verified' if passed else 'checks_failed')
