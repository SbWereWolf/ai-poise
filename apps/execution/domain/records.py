"""Execution-owned record values and identity rules, independent of persistence."""
from dataclasses import dataclass
import json
import math


class RecordsError(Exception):
    pass


def identifier(value, where):
    if type(value) is not str or not value or '\x00' in value:
        raise RecordsError(f'{where}: provide a nonempty string identity')
    return value


def integer(value, where, minimum):
    if type(value) is not int or value < minimum:
        raise RecordsError(f'{where}: provide an integer >= {minimum}')
    return value


def json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            json_value(item)
        return
    raise RecordsError('Record data must contain finite JSON values with string object keys')


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class ExecutionRecord:
    kind: str
    record_id: str
    task_id: str
    generation: int
    revision: int
    data: dict

    @classmethod
    def from_value(cls, value):
        fields = {'kind', 'id', 'task_id', 'generation', 'revision', 'data'}
        if type(value) is not dict or set(value) != fields:
            raise RecordsError('Provide a record with exactly kind,id,task_id,generation,revision,data')
        kind = value['kind']
        if type(kind) is not str or kind not in {
                'process_snapshot', 'contract_snapshot', 'attempt', 'evidence', 'history'}:
            raise RecordsError('Select a supported Execution record kind')
        record_id = identifier(value['id'], 'Record id')
        task_id = identifier(value['task_id'], 'Record task_id')
        generation = integer(value['generation'], 'Record generation', 1)
        revision = integer(value['revision'], 'Record revision', 1)
        data = value['data']
        if type(data) is not dict:
            raise RecordsError('Record data must be a JSON object')
        json_value(data)
        if 'task_id' in data and data['task_id'] != task_id:
            raise RecordsError(f'Record {record_id} data must refer to its own Task {task_id}')
        contract = data.get('contract')
        if (kind == 'contract_snapshot' and type(contract) is dict
                and 'task_id' in contract and contract['task_id'] != task_id):
            raise RecordsError(f'Contract snapshot {record_id} must refer to its own Task {task_id}')
        return cls(kind, record_id, task_id, generation, revision, data)

    def to_value(self):
        return {'kind': self.kind, 'id': self.record_id, 'task_id': self.task_id,
                'generation': self.generation, 'revision': self.revision, 'data': self.data}


def prepare_batch(request_id, expected_revision, records):
    identifier(request_id, 'Request id')
    integer(expected_revision, 'Expected revision', 0)
    if type(records) is not list or not records:
        raise RecordsError('Provide a nonempty record list')
    return tuple(ExecutionRecord.from_value(value) for value in records)


def decide_write(record, exact, latest):
    if exact is not None:
        if canonical_json(record.to_value()) != canonical_json(exact.to_value()):
            raise RecordsError(f'Record {record.kind}/{record.record_id} revision {record.revision} already has different data; use its original fact or a new identity')
        return False
    if latest is None:
        if record.revision != 1:
            raise RecordsError(f'Start new record {record.record_id} at revision 1')
    elif (record.kind != 'attempt' or record.generation != latest.generation
          or record.revision != latest.revision + 1):
        raise RecordsError(f'Preserve record {record.record_id} identity; append an attempt at its next revision and original generation')
    return True
