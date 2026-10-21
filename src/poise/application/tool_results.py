"""One complete-result use case shared by ordinary and controlled hook inputs."""
from copy import deepcopy

from ..modules.result_views.ports import ResultStore
from ..modules.result_views.tool_results import (
    ToolResultRequest, navigation, require_budget, presentation, inspect_source,
    hook_navigation, hook_presentation,
)


class ToolResults:
    def __init__(self, policy, store: ResultStore):
        self.policy, self.store = policy, store

    def execute(self, raw, audience):
        request = ToolResultRequest.parse(raw, self.policy).data
        operation = request['operation']; hook = operation == 'hook'
        if hook:
            normalized = {k: deepcopy(v) for k, v in request.items() if k != 'event'}
            normalized.update(operation='capture', tool={'kind': 'bash', 'name': 'Bash'},
                              response=deepcopy(request['event']['tool_response']))
        else: normalized = request
        if operation in ('capture', 'hook'):
            predicted = {'response_path': self.store.address(normalized['request_id']),
                         'capture': normalized['capture'], 'exit_code': normalized['exit_code']}
            if audience == 'agent':
                require_budget(hook_navigation(predicted, self.policy) if hook else navigation(predicted), self.policy)
            record = self.store.capture(normalized)
        else: record = self.store.load(request['response_path'])
        if operation in ('capture', 'view', 'hook'):
            if audience == 'machine': return record
            if hook:
                return hook_presentation(record, self.store.sources(record), self.policy)
            return presentation(record, self.store.sources(record), self.policy,
                                request['view'] if operation == 'view' else None)
        return inspect_source(record, self.store.source(record, request['channel']), request, self.policy,
                              audience=audience)

