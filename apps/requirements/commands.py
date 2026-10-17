"""App-owned creation use case over the narrow Task draft transaction port."""
from copy import deepcopy

from poise.modules.foundation.errors import DomainError
from .drafts import DraftTask


class DraftTaskCommands:
    def __init__(self, store, policy, max_items):
        self.store = store
        self.policy = policy
        self.max_items = max_items

    def create_task(self, request):
        draft = DraftTask.from_request(request)
        with self.store.task_drafts() as repository:
            previous = repository.creation(draft.intent['request_id'])
            if previous is not None:
                if previous['intent'] != draft.intent:
                    raise DomainError('Task creation request_id was used for another request')
                return {**deepcopy(previous['response']), 'replayed': True}
            task_id = draft.allocate(self.policy, repository.identifiers())
            return repository.create(task_id, draft, self.max_items)
