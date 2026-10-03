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
        if not isinstance(actual, dict) or actual.get('schema') != 'acceptance-manifest-1':
            raise DomainError('Invalid acceptance manifest schema')
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
