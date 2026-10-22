"""Application operations for durable execution facts; no runner or approval engine."""
import hashlib
import json

from apps.execution.domain.records import (
    ExecutionRecord, RecordsError, canonical_json, decide_write, identifier, prepare_batch,
)


class ExecutionRecords:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    @staticmethod
    def put_record(repository, record):
        if not repository.has_execution(record.task_id):
            raise RecordsError(f'Unknown execution {record.task_id}; set up the canonical Task before writing record {record.record_id}')
        exact = repository.find_version(record)
        latest = None if exact is not None else repository.latest(record.task_id, record.kind, record.record_id)
        if not decide_write(record, exact, latest):
            return False
        if record.kind == 'evidence' and record.data.get('attempt_id') is not None:
            attempt_id = identifier(record.data['attempt_id'], 'Evidence attempt_id')
            attempt = repository.latest(record.task_id, 'attempt', attempt_id)
            if attempt is None or attempt.generation != record.generation:
                raise RecordsError(f'Evidence {record.record_id} needs attempt {attempt_id} in Task {record.task_id}, generation {record.generation}')
        repository.insert(record)
        return True

    def write(self, *, request_id, expected_revision, records):
        parsed = prepare_batch(request_id, expected_revision, records)
        payload = {'operation': 'write_records', 'request_id': request_id,
                   'expected_revision': expected_revision,
                   'records': [record.to_value() for record in parsed]}
        digest = hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(request_id)
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError(f'Request {request_id} has different input; replay its original input or use a new request id')
                response = json.loads(prior['response_json'])
                response['replayed'] = True
                return response
            current = repository.get_revision()
            if current != expected_revision:
                raise RecordsError(f'Revision conflict: expected {expected_revision}, actual {current}; read the current revision before a new request')
            changed = sum(self.put_record(repository, record) for record in parsed)
            response = {'revision': current + 1, 'replayed': False, 'changed': changed}
            repository.set_revision(current + 1)
            repository.save_request(request_id, digest, response)
            return response

    def read(self, task_id):
        identifier(task_id, 'Task id')
        with self.unit_of_work(write=False) as repository:
            if not repository.has_execution(task_id):
                raise RecordsError(f'Unknown execution {task_id}; select an existing canonical Task')
            return [record.to_value() for record in repository.list_records(task_id)]

    def attempt(self, task_id, attempt_id):
        identifier(task_id, 'Task id')
        identifier(attempt_id, 'Attempt id')
        with self.unit_of_work(write=False) as repository:
            record = repository.latest(task_id, 'attempt', attempt_id)
            if record is None:
                raise RecordsError(f'Unknown attempt {attempt_id} in Task {task_id}; select its existing Task/attempt pair')
            return record.to_value()

    def setup_snapshot(self, repository, contract, contract_revision, generation):
        task_id = contract['task_id']
        record = ExecutionRecord.from_value({
            'kind': 'contract_snapshot', 'id': f'contract:{task_id}:{generation}',
            'task_id': task_id, 'generation': generation, 'revision': 1,
            'data': {'contract_revision': contract_revision, 'contract': contract},
        })
        self.put_record(repository, record)

    def setup_process_snapshot(self, repository, contract, process, generation):
        task_id = contract['task_id']
        record = ExecutionRecord.from_value({
            'kind': 'process_snapshot', 'id': f'process:{task_id}:{generation}',
            'task_id': task_id, 'generation': generation, 'revision': 1,
            'data': {'process_ref': contract['process_ref'], 'process': process},
        })
        self.put_record(repository, record)
