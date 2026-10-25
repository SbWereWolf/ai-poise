"""Short durable attempt phases around an independently retained command effect."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

from apps.execution.application.gates import GateCommands, internal_record_id
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.domain.process.workflow import RouteDefinition
from apps.execution.domain.records import ExecutionRecord, RecordsError
from apps.execution.domain.verification import ATTEMPT_KIND, VerificationPolicy
from apps.execution.infrastructure.verification_runner import VerificationRunner
from apps.execution.verification_protocol import verification_settings


class ExecutionVerification:
    def __init__(self, unit_of_work, config, state, evidence):
        self.unit_of_work, self.config = unit_of_work, config
        self.state, self.evidence = state, evidence

    def settings(self):
        return verification_settings(self.config.get('verification'),
            self.config['config_directory'], self.config.get('lifecycle'))

    def runner(self):
        return VerificationRunner(self.settings())

    @staticmethod
    def prior(repository, value, operation):
        prior = repository.get_request(value['request_id'])
        if prior is not None:
            if prior['digest'] != GateCommands.request_digest(operation, value):
                raise RecordsError('Verification request has different input; replay its original packet')
            return dict(json.loads(prior['response_json']), replayed=True)
        return None

    def binding(self, repository, value):
        if repository.get_revision() != value['expected_revision']:
            raise RecordsError('Verification revision conflict; read the current revision')
        execution = self.state.execution(repository, value['task_id'])
        if execution['generation'] != value['expected_generation']:
            raise RecordsError('Verification generation conflict')
        if execution['contract_revision'] != value['expected_contract_revision']:
            raise RecordsError('Verification contract revision conflict')
        role = LifecyclePolicy.actor(self.config.get('lifecycle'), value['actor_id'])
        if role not in self.settings()['roles']:
            raise RecordsError('Verification requires a configured actor role')
        return execution, role

    def candidate(self, repository, execution, context_id, candidate_id):
        task, generation = execution['task_id'], execution['generation']
        context = repository.latest(task, 'gate_context', internal_record_id('gate_context', generation, context_id))
        gate = repository.latest(task, 'stage_result', internal_record_id('stage_result', generation, context_id, candidate_id))
        if context is None or gate is None or not gate.data['assessment']['passed']:
            raise RecordsError('Select the accepted current Gate context/candidate')
        provenance = gate.data['provenance']
        for key, expected in [('stage_id', execution['current_stage']),
                              ('iteration', execution['iteration']),
                              ('contract_revision', execution['contract_revision'])]:
            if provenance[key] != expected:
                raise RecordsError('Current Gate ' + key + ' conflicts with verification')
        return context, gate

    def load(self, repository, execution, attempt_id, *, current=True, check_source=True):
        record = repository.latest(execution['task_id'], ATTEMPT_KIND, attempt_id)
        if record is None:
            raise RecordsError('Select an existing own verification attempt_id')
        data = deepcopy(record.data)
        if current:
            a = data['value']
            if any(a[k] != execution[k] for k in ('generation', 'contract_revision', 'iteration')) or a['stage_id'] != execution['current_stage']:
                raise RecordsError('Verification attempt belongs to an earlier generation or stage')
            if check_source and self.runner().source() != a['source']:
                raise RecordsError('Verification source changed; preserve the old attempt and agree rework')
        return data

    @staticmethod
    def save(repository, data):
        value = data['value']
        prior = repository.latest(value['task_id'], ATTEMPT_KIND, value['attempt_id'])
        repository.insert(ExecutionRecord(ATTEMPT_KIND, value['attempt_id'], value['task_id'],
            value['generation'], 1 if prior is None else prior.revision + 1, deepcopy(data)))

    @staticmethod
    def response(repository, value, data, status, *, save=True, changed=None):
        revision = repository.get_revision()
        result = {'schema': 'stride-execution/v1', 'status': status,
                  'revision': revision + int(save), 'replayed': False,
                  'changed': save if changed is None else changed,
                  'attempt': deepcopy(data['value'])}
        if save:
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], GateCommands.request_digest('attempt', value), result)
        return result

    def begin_in(self, repository, execution, value, purpose='stage', owner_id=None, method_ids=None):
        self.candidate(repository, execution, value['context_id'], value['candidate_id'])
        if repository.latest(value['task_id'], ATTEMPT_KIND, value['attempt_id']) is not None:
            raise RecordsError('Attempt identity already exists; use its retained attempt')
        process = self.state.process(repository, execution)
        role = LifecyclePolicy.actor(self.config.get('lifecycle'), value['actor_id'])
        if role != RouteDefinition.from_workflow(process).node(execution['current_stage']).role:
            raise RecordsError('Current verification stage requires its declared actor role')
        schedule = (method_ids if method_ids is not None else next(
            s['methods'] for s in execution['contract']['stage_expectations']
            if s['stage_id'] == execution['current_stage']))
        if value['method_ids'] != schedule:
            raise RecordsError('Verification method_ids must match the current declared schedule')
        if self.runner().source() != value['source']:
            raise RecordsError('Verification source must match current repository commit/tree')
        records = repository.list_records(value['task_id'])
        VerificationPolicy.require_resolved(records, execution)
        budget = VerificationPolicy.budget(records, execution, self.settings()['max_attempts'])
        attempt = VerificationPolicy.reserve(execution, value, budget, purpose,
                                             value['candidate_id'] if owner_id is None else owner_id)
        by_id = {m['id']: m for m in execution['contract']['methods']}
        data = {'value': attempt, 'scope': VerificationPolicy.scope(records, execution),
                'methods': [deepcopy(by_id[mid]) for mid in schedule],
                'execution_key': str(uuid4()), 'requests': {}, 'actor_id': value['actor_id']}
        self.save(repository, data)
        return data

    def finish_in(self, repository, execution, data, evidence_work):
        attempt = data['value']
        if attempt['state'] not in ('reserved', 'running'):
            raise RecordsError('Attempt is already resolved; read its saved result')
        if any(run['receipt'] is None for run in attempt['runs']):
            raise RecordsError('Complete every reserved run before finishing its attempt')
        VerificationPolicy.require_quiescent(attempt)
        self.evidence.plan(self.state.process(repository, execution), execution['contract']).validate_work(
            attempt['stage_id'], evidence_work)
        evidence_id = str(uuid4())
        self.state.put(repository, execution, 'attempt', attempt['attempt_id'],
                       VerificationPolicy.generic_attempt(data))
        self.state.put(repository, execution, 'evidence', evidence_id,
                       {'attempt_id': attempt['attempt_id'], 'evidence_work': deepcopy(evidence_work)})
        attempt.update(state='finished', evidence_id=evidence_id)
        self.save(repository, data)
        return data

    def execute(self, value):
        self.settings()
        if value['action'] in ('run', 'recover'):
            return self.start_run(value)
        with self.unit_of_work(write=True) as repository:
            prior = self.prior(repository, value, 'attempt')
            if prior is not None:
                return prior
            execution, _ = self.binding(repository, value)
            if value['action'] == 'begin':
                data = self.begin_in(repository, execution, value)
                status = 'attempt_reserved'
            else:
                data = self.load(repository, execution, value['attempt_id'],
                                 check_source=value['action'] != 'abandon')
                VerificationPolicy.require_actor(data, value['actor_id'])
                if value['action'] == 'finish':
                    self.finish_in(repository, execution, data, value['evidence_work'])
                    status = 'attempt_finished'
                else:
                    if data['value']['state'] not in ('reserved', 'running'):
                        raise RecordsError('Attempt is already resolved')
                    VerificationPolicy.require_quiescent(data['value'])
                    data['value']['state'] = 'abandoned'
                    self.save(repository, data)
                    status = 'attempt_abandoned'
            return self.response(repository, value, data, status)

    def start_run(self, value):
        runner = self.runner()
        started_now = False
        with self.unit_of_work(write=True) as repository:
            prior = self.prior(repository, value, 'attempt')
            if prior is not None:
                return prior
            execution = self.state.execution(repository, value['task_id'])
            data = self.load(repository, execution, value['attempt_id'],
                             check_source=value['action'] != 'recover')
            VerificationPolicy.require_actor(data, value['actor_id'])
            run = VerificationPolicy.validate_run(data['value'], value['run_id'])
            if value['action'] == 'run' and run['started']:
                if run['receipt'] is not None:
                    raise RecordsError('Run already has its recorded result; use the original request or read its attempt')
                original = data['requests'][run['run_id']]
                if original['request_id'] == value['request_id']:
                    if original['digest'] != GateCommands.request_digest('attempt', value):
                        raise RecordsError('Started run request has different input; use its original packet')
                else:
                    self.binding(repository, value)
                return self.response(repository, value, data, 'pending', save=False)
            self.binding(repository, value)
            if value['action'] == 'recover':
                if not run['started']:
                    raise RecordsError('Only a started run can recover its actual capture')
                receipt = runner.recover(data, run)
                if receipt is None:
                    return self.response(repository, value, data, 'pending', save=False)
            else:
                run['started'] = True
                data['value']['state'] = 'running'
                data['requests'][run['run_id']] = {'request_id': value['request_id'],
                    'digest': GateCommands.request_digest('attempt', value)}
                self.save(repository, data)
                repository.set_revision(repository.get_revision() + 1)
                started_now = True
                receipt = None
            process = self.state.process(repository, execution)
            subject_rules = execution['contract']['evidence_plan'][data['value']['stage_id']]['subject_methods']
        if receipt is None:
            method = next(m for m in data['methods'] if m['id'] == run['method_id'])
            receipt = runner.run(data, run, method, subject_rules)
        try:
            return self.record_run(value, receipt)
        except sqlite3.Error:
            with self.unit_of_work(write=False) as repository:
                execution = self.state.execution(repository, value['task_id'])
                data = self.load(repository, execution, value['attempt_id'], check_source=False)
                return self.response(repository, value, data, 'pending', save=False, changed=started_now)

    def record_run(self, value, receipt):
        with self.unit_of_work(write=True) as repository:
            prior = self.prior(repository, value, 'attempt')
            if prior is not None:
                return prior
            execution = self.state.execution(repository, value['task_id'])
            data = self.load(repository, execution, value['attempt_id'], check_source=False)
            VerificationPolicy.require_actor(data, value['actor_id'])
            run = VerificationPolicy.validate_run(data['value'], value['run_id'])
            if not run['started']:
                raise RecordsError('Record only an actually started reserved run')
            if run['receipt'] is not None and run['receipt'] != receipt:
                raise RecordsError('Run already has another actual receipt')
            changed = run['receipt'] is None
            if changed:
                run.update(receipt=deepcopy(receipt), termination=deepcopy(receipt['termination']))
                self.save(repository, data)
            return self.response(repository, value, data, 'run_recorded', changed=changed)

    def query(self, repository, kind, value):
        execution = self.state.execution(repository, value['task_id'])
        if kind == 'verification':
            return VerificationPolicy.budget(repository.list_records(value['task_id']), execution,
                                             self.settings()['max_attempts'])
        self.settings()
        return self.load(repository, execution, value['attempt_id'], current=False)['value']
