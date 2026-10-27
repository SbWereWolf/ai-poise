"""Explicit stage-result choices; no storage, file reads or inferred decisions."""
from copy import deepcopy
from dataclasses import dataclass

from ..artifact_factory.domain import exact, relative
from ..foundation.errors import DomainError


def task_read_packet():
    return {'operation': 'show', 'input': {'queries': [{'id': 'task', 'kind': 'task'}]}, 'messages': []}


class SelectionRefusal(DomainError):
    def __init__(self, code, detail, *, kind='correct_input', packet=None):
        super().__init__(detail if code is None else f'{code}: {detail}')
        self.continuation = {'kind': kind, 'packet': packet, 'instruction': detail}


@dataclass(frozen=True)
class SelectedResultIntent:
    value: dict

    @classmethod
    def parse(cls, raw):
        try:
            exact(raw, {'schema', 'request_id', 'expected', 'sections', 'metadata', 'files'}, 'selection')
            if raw['schema'] != 'stage-result-selection-1':
                raise DomainError('selection.schema must be stage-result-selection-1')
            if not isinstance(raw['request_id'], str) or not raw['request_id'].strip() or '\0' in raw['request_id']:
                raise DomainError('selection.request_id must be a nonempty identity without NUL')
            expected = raw['expected']
            exact(expected, {'task', 'stage', 'iteration', 'version'}, 'selection.expected')
            for key in ('task', 'stage'):
                if not isinstance(expected[key], str) or not expected[key].strip() or '\0' in expected[key]:
                    raise DomainError(f'selection.expected.{key} must be an explicit identifier')
            for key, minimum in (('iteration', 1), ('version', 0)):
                if type(expected[key]) is not int or expected[key] < minimum:
                    raise DomainError(f'selection.expected.{key} must be an integer >= {minimum}')
            if not isinstance(raw['sections'], dict) or any(
                    not isinstance(k, str) or not k or not isinstance(v, str)
                    for k, v in raw['sections'].items()):
                raise DomainError('selection.sections must map section identifiers to text')
            fields = {'artifact_paths', 'commit_message', 'content_additions', 'trace',
                      'method_additions', 'stage_work', 'evidence_work'}
            if not isinstance(raw['metadata'], dict) or set(raw['metadata']) - fields:
                raise DomainError('selection.metadata contains unsupported fields or is not an object')
            if not isinstance(raw['files'], list):
                raise DomainError('selection.files must be an explicit list')
            for spec in raw['files']:
                exact(spec, {'scope', 'path', 'source'}, 'selected file')
                if spec['scope'] not in ('task', 'sprint', 'runtime'):
                    raise DomainError(f'Invalid selected destination owner: {spec["scope"]}')
                relative(spec['path'])
                exact(spec['source'], {'root', 'path'}, 'selected source')
                source = spec['source']
                if source['root'] not in ('task', 'worktree', 'runtime'):
                    raise DomainError(f'Invalid selected source root/path: {source}')
                try:
                    relative(source['path'])
                except DomainError as exc:
                    raise DomainError(f'Selected source {source}: {exc}') from exc
        except DomainError as exc:
            raise SelectionRefusal('selection_contract_invalid', str(exc)) from exc
        return cls(deepcopy(raw))

    def compose_result(self, template, required):
        sections = self.value['sections']
        unknown = set(sections) - set(template['sections'])
        if unknown:
            raise SelectionRefusal('selection_contract_invalid', f'Undeclared writable sections: {sorted(unknown)}')
        missing = [key for key in required if key not in sections or not sections[key].strip()
                   or sections[key] == required[key]]
        if missing:
            raise SelectionRefusal('selection_missing_sections', f'Provide substantive sections: {missing}')
        payload = deepcopy(template)
        payload['sections'].update(deepcopy(sections))
        payload.update(deepcopy(self.value['metadata']))
        return payload


def selection_result(result, intent, progression):
    request_id = intent.value['request_id']
    if result['status'] == 'verified':
        args = {'request_id': request_id + '-advance', 'task_id': result['task']}
        if progression is not None:
            args['request_id'] = progression['request_id']
            if progression.get('target_stage') is not None:
                args['target_stage'] = progression['target_stage']
        continuation = {'kind': 'advance', 'packet': {'operation': 'advance', 'input': args, 'messages': []},
                        'instruction': 'Continue the saved result through advance; its ownership, role and acceptance gates still apply.'}
    else:
        continuation = {'kind': 'continue_work', 'packet': None,
                        'instruction': f'Inspect the saved {result["status"]} result and current context; correct its stated obstacle before a new submission.'}
    return {**result, 'selection': {'schema': intent.value['schema'], 'request_id': request_id},
            'continuation': continuation}
