"""Standalone draft intent and Task identity decisions, without persistence."""
from copy import deepcopy
from dataclasses import dataclass

from poise.modules.foundation.errors import DomainError


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f'{name} must be a nonempty identifier')
    return value


@dataclass(frozen=True)
class DraftTask:
    intent: dict

    @classmethod
    def from_request(cls, request):
        required = {'request_id', 'goal'}
        optional = {'task_id', 'task_requirements', 'definition_of_done', 'work_choice'}
        if (not isinstance(request, dict) or not required <= request.keys()
                or request.keys() - required - optional):
            raise DomainError('Task creation requires request_id and goal with supported draft fields')
        _identifier(request['request_id'], 'Task creation request_id')
        if 'task_id' in request:
            _identifier(request['task_id'], 'Task ID')
        if 'work_choice' in request:
            choice = request['work_choice']
            if not isinstance(choice, dict):
                raise DomainError('Task work_choice must be an explicit object')
            if choice == {'kind': 'standalone'}:
                pass
            elif set(choice) == {'kind', 'sequence_id'} and choice['kind'] == 'sequence':
                _identifier(choice['sequence_id'], 'Task sequence_id')
            else:
                raise DomainError('Task work_choice requires standalone or sequence with sequence_id')
        return cls(deepcopy(request))

    def statement(self):
        # Omitted materials are an agreed goal-only draft, not inferred readiness.
        return {
            'goal': self.intent['goal'],
            'task_requirements': deepcopy(self.intent.get('task_requirements', [])),
            'definition_of_done': deepcopy(self.intent.get('definition_of_done', [])),
        }

    def allocate(self, policy, occupied_ids):
        if 'task_id' in self.intent:
            identifier = self.intent['task_id']
            if identifier in occupied_ids:
                raise DomainError(f'Task ID already exists: {identifier}')
            return identifier
        for candidate in policy.candidates():
            if candidate not in occupied_ids:
                return candidate
        raise DomainError('Task ID allocation namespace is exhausted')
