"""Own result acceptance composed with the existing core transaction/record owner."""
import hashlib
import json

from apps.execution.domain.gates import (GateContext, assess_candidate, prepare_candidate,
                                         snapshot_from_value, snapshot_value)
from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.records import ExecutionRecord, RecordsError, canonical_json


def internal_record_id(kind, generation, context_id, candidate_id=None):
    parts = [generation, context_id]
    if candidate_id is not None:
        parts.append(candidate_id)
    return kind + ':' + canonical_json(parts)


def _scope(record, value):
    binding = record.data if record.kind == 'gate_context' else record.data['provenance']
    return (record.task_id == value['task_id'] and record.generation == value['generation']
            and binding['context_id'] == value['context_id'])


class GateCommands:
    def __init__(self, unit_of_work, files):
        self.unit_of_work = unit_of_work
        self.files = files

    def execute(self, value):
        digest = hashlib.sha256(canonical_json({'operation': 'gate', 'input': value}).encode()).hexdigest()
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(value['request_id'])
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError('Gate request has different input; use the original packet or a new request_id')
                response = json.loads(prior['response_json'])
                response['replayed'] = True
                return response
            revision = repository.get_revision()
            if revision != value['expected_revision']:
                raise RecordsError(f'Gate revision conflict: expected {value["expected_revision"]}, actual {revision}')
            result = (self._declare(repository, value) if value['action'] == 'declare'
                      else self._evaluate(repository, value))
            response = {'schema': 'stride-execution/v1', 'revision': revision + 1,
                        'replayed': False, **result}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    @staticmethod
    def _binding(repository, task_id, generation, contract_revision=None):
        execution = repository.execution(task_id)
        if execution is None:
            raise RecordsError(f'Unknown execution {task_id}; set up the canonical Task first')
        if execution['generation'] != generation:
            raise RecordsError('Gate generation must match the canonical execution generation')
        if contract_revision is not None and execution['contract_revision'] != contract_revision:
            raise RecordsError('Gate contract_revision must match the saved canonical contract')
        contract = repository.latest(task_id, 'contract_snapshot', f'contract:{task_id}:{generation}')
        process = repository.latest(task_id, 'process_snapshot', f'process:{task_id}:{generation}')
        if contract is None or contract.generation != generation or contract.revision != 1:
            raise RecordsError('Gate needs the canonical setup contract snapshot')
        if (contract.data['contract_revision'] != execution['contract_revision']
                or canonical_json(contract.data['contract']) != canonical_json(json.loads(execution['contract_json']))):
            raise RecordsError('Gate needs the contract snapshot belonging to the canonical execution')
        saved = contract.data['contract']
        if process is None or process.generation != generation or process.revision != 1:
            raise RecordsError('Gate needs the frozen process snapshot created by process-enabled setup')
        if (process.data.get('process_ref') != saved['process_ref']
                or not isinstance(process.data.get('process'), dict)
                or process.data['process'].get('goal_type') != saved['process_ref']['goal_type']):
            raise RecordsError('Gate needs the frozen process snapshot for the canonical process_ref')
        return saved, process.data['process'], execution['contract_revision']

    @staticmethod
    def _insert(repository, kind, value, data):
        candidate = None if kind == 'gate_context' else value['candidate_id']
        record = ExecutionRecord(kind, internal_record_id(kind, value['generation'], value['context_id'], candidate),
                                 value['task_id'], value['generation'], 1, data)
        repository.insert(record)
        return record

    def _declare(self, repository, value):
        contract, process, revision = self._binding(repository, value['task_id'], value['generation'],
                                                     value['contract_revision'])
        GateContext.parse(process, contract, value)
        data = {key: value[key] for key in ('context_id', 'contract_revision', 'requirement_ids',
                                           'task_content', 'stage_contracts')}
        data.update(process_ref=contract['process_ref'], requirements_snapshot_revision=contract['requirements_snapshot_revision'],
                    admission_id=contract['admission_id'], request_id=value['request_id'])
        rid = internal_record_id('gate_context', value['generation'], value['context_id'])
        existing = repository.latest(value['task_id'], 'gate_context', rid)
        if existing is not None:
            previous = {k: v for k, v in existing.data.items() if k != 'request_id'}
            proposed = {k: v for k, v in data.items() if k != 'request_id'}
            if canonical_json(previous) != canonical_json(proposed):
                raise RecordsError('Gate context already has different contents; preserve its identity')
        else:
            self._insert(repository, 'gate_context', value, data)
        return {'status': 'declared', 'task_id': value['task_id'], 'generation': value['generation'],
                'context_id': value['context_id'], 'changed': existing is None}

    def _evaluate(self, repository, value):
        rid = internal_record_id('gate_context', value['generation'], value['context_id'])
        context_record = repository.latest(value['task_id'], 'gate_context', rid)
        if context_record is None:
            raise RecordsError('Select an existing own gate context before evaluating a candidate')
        contract, process, revision = self._binding(repository, value['task_id'], value['generation'],
                                                     context_record.data['contract_revision'])
        context = GateContext.parse(process, contract, context_record.data)
        context.stage(value['stage_id'])
        rid = internal_record_id('gate_candidate', value['generation'], value['context_id'], value['candidate_id'])
        existing = repository.latest(value['task_id'], 'gate_candidate', rid)
        submitted = {key: value[key] for key in ('sections', 'trace', 'files')}
        if existing is not None:
            provenance = existing.data['provenance']
            if (canonical_json(existing.data['submitted']) != canonical_json(submitted)
                    or any(provenance[key] != value[key] for key in ('stage_id', 'iteration'))):
                raise RecordsError('Gate candidate already has different input; use a new candidate_id')
            decision_id = internal_record_id('gate_decision', value['generation'], value['context_id'], value['candidate_id'])
            decision = repository.latest(value['task_id'], 'gate_decision', decision_id)
            return self._outcome(value, decision.data)
        records = repository.list_records(value['task_id'])
        history = [record for record in records
                   if record.kind == 'gate_candidate' and _scope(record, value)]
        # A prepared candidate can fail its gates and still retain submitted
        # content, just as original submit preceded content/file assessment.
        decisions = {record.record_id: record for record in records
                     if record.kind == 'gate_decision' and _scope(record, value)}
        previous = None
        for candidate in reversed(history):
            did = internal_record_id('gate_decision', value['generation'], value['context_id'],
                                     candidate.data['provenance']['candidate_id'])
            if decisions[did].data['assessment']['prepared']:
                previous = candidate.data
                break
        snapshot = snapshot_from_value(None if previous is None else previous['snapshot'])
        catalogue = [] if previous is None else previous['catalogue']
        error = None
        try:
            prepared = prepare_candidate(context, snapshot, value['stage_id'], value['sections'], value['trace'])
        except DomainError as exc:
            prepared, error = snapshot, str(exc)
        facts, files, errors = (), [], []
        if error is None:
            facts, files, catalogue, errors = self.files.observe(catalogue, value['files'])
        assessment = assess_candidate(context, value['stage_id'], prepared, facts, errors, error)
        provenance = {key: value[key] for key in ('task_id', 'generation', 'context_id', 'candidate_id',
                                                'stage_id', 'iteration', 'request_id')}
        provenance.update(contract_revision=revision, process_ref=contract['process_ref'],
                          requirements_snapshot_revision=contract['requirements_snapshot_revision'],
                          admission_id=contract['admission_id'])
        snapshot_data = snapshot_value(prepared)
        self._insert(repository, 'gate_candidate', value,
                     {'provenance': provenance, 'submitted': submitted, 'snapshot': snapshot_data,
                      'files': files, 'catalogue': catalogue})
        accepted = assessment['passed']
        result_id = internal_record_id('stage_result', value['generation'], value['context_id'], value['candidate_id']) if accepted else None
        decision = {'provenance': provenance, 'assessment': assessment, 'accepted': accepted, 'result_id': result_id}
        self._insert(repository, 'gate_decision', value, decision)
        if accepted:
            self._insert(repository, 'stage_result', value,
                         {'provenance': provenance, 'snapshot': snapshot_data, 'files': files, 'assessment': assessment})
        return self._outcome(value, decision)

    @staticmethod
    def _outcome(value, decision):
        return {'status': 'evaluated', **{key: value[key] for key in
                ('task_id', 'generation', 'context_id', 'candidate_id', 'stage_id', 'iteration')},
                'accepted': decision['accepted'], 'result_id': decision['result_id'],
                'assessment': decision['assessment']}


class GateQueries:
    @staticmethod
    def resolve(query, records):
        def find(kind):
            candidate = query.get('candidate_id') if kind != 'gate_context' else None
            rid = internal_record_id(kind, query['generation'], query['context_id'], candidate)
            value = next((r for r in records if r.kind == kind and r.record_id == rid
                          and _scope(r, query)), None)
            return None if value is None else value.to_value()
        result = {'id': query['id'], 'kind': query['kind']}
        if query['kind'] == 'gate_context':
            result['context'] = find('gate_context')
        else:
            result.update(candidate=find('gate_candidate'), decision=find('gate_decision'),
                          accepted_result=find('stage_result'))
        return result
