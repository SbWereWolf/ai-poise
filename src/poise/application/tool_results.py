"""One complete-result use case shared by ordinary and controlled hook inputs."""
from copy import deepcopy

from ..modules.result_views.ports import ResultStore
from ..modules.result_views.tool_results import (
    ToolResultRequest, navigation, require_budget, presentation, inspect_source,
    hook_navigation, hook_presentation,
    prepare_views, encoded,
)


class ToolResults:
    def __init__(self, policy, store: ResultStore, native_source=None):
        self.policy, self.store = policy, store
        self.native_source = native_source

    def execute(self, raw, audience):
        if 'session' in self.policy and len(encoded(raw).encode()) > self.policy['input']['max_bytes']:
            from ..modules.result_views.tool_results import reject
            reject()
        request = ToolResultRequest.parse(raw, self.policy).data
        operation = request['operation']; hook = operation == 'hook'
        if operation in ('expanded', 'full'):
            return self.saved_service(operation)
        if 'session' in self.policy:
            return self.prepared(request, audience)
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

    def prepared(self, request, audience):
        operation = request['operation']
        if operation in ('capture', 'hook'):
            normalized = deepcopy(request)
            selected_parser = None
            source_text = None
            if operation == 'hook':
                source = self.native_source(request['event'])
                if source.pop('pending'):
                    return {'status': 'pending', 'capture': source['capture'], 'exit_code': None}
                selected_parser = source.pop('_selected_parser')
                if selected_parser['format'] == 'native-text':
                    source_text = source['response']
                normalized = {k: deepcopy(v) for k, v in request.items() if k != 'event'}
                normalized.update(source, operation='capture')
            record = self.store.capture(normalized,
                prepare=lambda record, sources: self.prepare(record, sources, audience, selected_parser),
                source_text=source_text)
        else:
            record = self.store.load(request['response_path'])
            self.store.validate_prepared(record)
        if audience == 'machine' and operation in ('capture', 'hook', 'view'): return record
        if operation in ('capture', 'hook', 'view'):
            result = navigation(record, request.get('view'))
            result.update({k: record[k] for k in ('run_id', 'full_path', 'expanded_path', 'last_path')})
            mode = request.get('view', 'brief')
            if mode != 'full':
                result['preview'] = (record['_prepared']['brief'] if mode == 'brief'
                                     else self.store.expanded(record))
            require_budget(result, self.policy)
            return result
        return inspect_source(record, self.store.source(record, request['channel']), request, self.policy,
                              audience=audience)

    def prepare(self, record, sources, audience, selected_parser=None):
        pair = prepare_views(record, sources, self.policy, selected_parser=selected_parser)
        if audience == 'agent':
            candidate = navigation(record)
            candidate.update({k: record[k] for k in ('run_id', 'full_path', 'expanded_path', 'last_path')})
            candidate['preview'] = pair['brief']
            require_budget(candidate, self.policy)
        return pair

    def saved_service(self, operation):
        import hashlib
        import json
        from ..modules.result_views.tool_results import reject
        record = self.store.latest()
        if operation == 'full' and record['capture']['state'] != 'complete':
            reject('source_incomplete', 'capture', 'inspect:source')
        result = {'status': operation, 'run_id': record['run_id'], 'purpose': 'harness-service',
                  'capability': 'tool-result', 'capture': deepcopy(record['capture']),
                  'policy_sha256': hashlib.sha256(json.dumps(self.policy, ensure_ascii=False,
                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        if operation == 'expanded': result['text'] = self.store.expanded(record)
        else: result['full_path'] = record['full_path']
        require_budget(result, self.policy)
        return result

