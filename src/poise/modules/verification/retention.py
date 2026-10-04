"""Declared retained inputs and complete immutable acceptance inventories."""
from copy import deepcopy
from dataclasses import dataclass
import re

from ..artifact_factory.domain import relative, exact
from ..foundation.errors import DomainError


def checksum(value, length, label):
    if (not isinstance(value, str) or len(value) != length
            or any(c not in '0123456789abcdef' for c in value)):
        raise DomainError(f'{label}: invalid digest/commit')


@dataclass(frozen=True)
class ArtifactInputs:
    manifest_directory: str
    files: tuple[dict, ...]

    @classmethod
    def parse(cls, raw, environment):
        exact(raw, {'manifest_directory', 'files'}, 'artifact_inputs')
        directory = relative(raw['manifest_directory'])
        if not isinstance(raw['files'], list):
            raise DomainError('artifact_inputs.files must be a list')
        ids, paths, names = set(), set(), set()
        for item in raw['files']:
            exact(item, {'id', 'path', 'digest', 'producer', 'input_ref', 'environment'},
                  'artifact input')
            identifier = item['id']
            if (not isinstance(identifier, str) or not identifier
                    or '/' in identifier or '\\' in identifier or identifier in ('.', '..')):
                raise DomainError('artifact input id must be a safe identifier')
            path = relative(item['path'])
            checksum(item['digest'], 64, f'input {identifier}')
            exact(item['producer'], {'task_id', 'commit'}, 'artifact input producer')
            if not isinstance(item['producer']['task_id'], str) or not item['producer']['task_id']:
                raise DomainError(f'input {identifier}: producer task_id required')
            checksum(item['producer']['commit'], 40, f'input {identifier} producer')
            if not isinstance(item['input_ref'], str) or not item['input_ref'].strip():
                raise DomainError(f'input {identifier}: input_ref required')
            name = item['environment']
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name)
                    or name == 'POISE_RUN_OUTPUT_DIR' or name in environment):
                raise DomainError(f'input {identifier}: invalid/conflicting environment binding')
            if identifier in ids or path in paths or name in names:
                raise DomainError(f'input {identifier}: duplicate id/path/environment')
            ids.add(identifier); paths.add(path); names.add(name)
        return cls(directory, tuple(deepcopy(raw['files'])))


class AcceptanceManifest:
    @staticmethod
    def build(task_id, method_id, commit, tree, inputs, evidence, receipt, definition_digest):
        return {
            'schema': 'acceptance-manifest-1', 'task_id': task_id,
            'method_id': method_id, 'commit': commit,
            'tree': tree,
            'inputs': deepcopy(inputs), 'evidence': deepcopy(evidence),
            'receipt': {
                'id': receipt['id'], 'actual_exit_code': receipt['actual_exit_code'],
                'passed': receipt['passed'], 'definition_digest': definition_digest,
            },
        }

    @staticmethod
    def validate(actual, expected):
        AcceptanceManifest._schema(actual)
        for key in ('task_id', 'method_id', 'commit', 'tree', 'receipt'):
            if actual.get(key) != expected[key]:
                raise DomainError(f'Acceptance manifest candidate/{key} mismatch')
        if actual.get('inputs') != expected['inputs']:
            identifiers = ','.join(item['id'] for item in expected['inputs'])
            raise DomainError(f'Acceptance manifest input inventory mismatch: {identifiers}')
        if actual.get('evidence') != expected['evidence']:
            raise DomainError('Acceptance manifest proof inventory mismatch')
        if set(actual) != set(expected):
            raise DomainError('Acceptance manifest has unknown fields')

    @staticmethod
    def _strings(record, fields, label):
        for field in fields:
            value = record[field]
            if type(value) is not str:
                raise DomainError(f'Acceptance manifest {label}{field}: invalid type')
            if not value.strip():
                raise DomainError(f'Acceptance manifest {label}{field}: empty string')

    @classmethod
    def _schema(cls, actual):
        exact(actual, {'schema', 'task_id', 'method_id', 'commit', 'tree',
                       'inputs', 'evidence', 'receipt'}, 'Acceptance manifest')
        cls._strings(actual, ('schema', 'task_id', 'method_id', 'commit', 'tree'), '')
        if actual['schema'] != 'acceptance-manifest-1':
            raise DomainError('Invalid acceptance manifest schema')
        for field in ('commit', 'tree'):
            checksum(actual[field], 40, f'Acceptance manifest {field}')
        receipt = actual['receipt']
        exact(receipt, {'id', 'actual_exit_code', 'passed', 'definition_digest'},
              'Acceptance manifest receipt')
        cls._strings(receipt, ('id', 'definition_digest'), 'receipt.')
        checksum(receipt['definition_digest'], 64, 'Acceptance manifest receipt.definition_digest')
        if type(receipt['actual_exit_code']) is not int:
            raise DomainError('Acceptance manifest receipt.actual_exit_code: invalid type')
        if type(receipt['passed']) is not bool:
            raise DomainError('Acceptance manifest receipt.passed: invalid type')
        for field in ('inputs', 'evidence'):
            if type(actual[field]) is not list:
                raise DomainError(f'Acceptance manifest {field}: invalid type')
        for item in actual['inputs']:
            exact(item, {'id', 'path', 'digest', 'producer', 'input_ref'},
                  'Acceptance manifest input')
            cls._strings(item, ('id', 'path', 'digest', 'input_ref'), 'input.')
            checksum(item['digest'], 64, 'Acceptance manifest input.digest')
            producer = item['producer']
            exact(producer, {'task_id', 'commit'}, 'Acceptance manifest input.producer')
            cls._strings(producer, ('task_id', 'commit'), 'input.producer.')
            checksum(producer['commit'], 40, 'Acceptance manifest input.producer.commit')
        for item in actual['evidence']:
            if type(item) is not dict or type(item.get('kind')) is not str:
                raise DomainError('Acceptance manifest evidence.kind: invalid type')
            kind = item['kind']
            if kind not in ('stdout', 'stderr', 'output'):
                raise DomainError('Acceptance manifest evidence.kind: unknown kind')
            fields = {'kind', 'path', 'digest'} | ({'id'} if kind == 'output' else set())
            exact(item, fields, 'Acceptance manifest evidence')
            cls._strings(item, tuple(sorted(fields)), 'evidence.')
            checksum(item['digest'], 64, 'Acceptance manifest evidence.digest')
