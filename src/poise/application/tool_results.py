"""One complete-result use case shared by ordinary and controlled hook inputs."""
from copy import deepcopy
import hashlib
import json

from ..modules.result_views.ports import ResultStore, UnknownInputRegistry, UnknownInputDispatcher
from ..modules.result_views.tool_results import (
    ToolResultRequest, navigation, require_budget, presentation, inspect_source,
    hook_navigation, hook_presentation,
    prepare_views, encoded,
)


class ToolResults:
    def __init__(self, policy, store: ResultStore, native_source=None, *, unknown_registry: UnknownInputRegistry | None = None,
                 unknown_dispatcher: UnknownInputDispatcher | None = None):
        self.policy, self.store = policy, store
        self.native_source = native_source
        self.unknown_registry, self.unknown_dispatcher = unknown_registry, unknown_dispatcher

    def execute(self, raw, audience):
        if 'session' in self.policy and len(encoded(raw).encode()) > self.policy['input']['max_bytes']:
            from ..modules.result_views.tool_results import reject
            reject()
        request = ToolResultRequest.parse(raw, self.policy).data
        operation = request['operation']; hook = operation == 'hook'
        if operation == 'register_unknown':
            result = self.unknown_registry.register(request['observation'])
            if audience == 'agent': require_budget(result, self.policy)
            return result
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
                pending = source.pop('pending')
                if pending:
                    normalized['request_id'] += ':pending:' + hashlib.sha256(
                        json.dumps(request['event']['tool_response'], sort_keys=True).encode()).hexdigest()
                selected_parser = source.pop('_selected_parser')
                if selected_parser['format'] == 'native-text':
                    source_text = source['response']
                snapshot_id = normalized['request_id']
                normalized = {k: deepcopy(v) for k, v in request.items() if k != 'event'}
                normalized.update(source, operation='capture', request_id=snapshot_id)
            if operation == 'capture':
                from ..modules.result_views.unknown_inputs import generic_profile
                from ..modules.result_views.tool_results import native_snapshot, ToolResultFailure
                selected_parser = self.policy['parsers'].get(request['tool']['name'])
                if selected_parser is None:
                    selected_parser = generic_profile('unknown_tool')
                elif selected_parser.get('parser') in ('bash', 'mcp', 'poise'):
                    try:
                        native_snapshot({'tool_name': request['tool']['name'], 'tool_response': request['response']},
                                        self.policy, selected=(request['tool']['name'], selected_parser))
                    except ToolResultFailure as error:
                        if error.code != 'unmapped_input': raise
                        selected_parser = generic_profile('unknown_format')
            record = self.store.capture(normalized,
                prepare=lambda record, sources: self.prepare(record, sources, audience, selected_parser, request),
                source_text=source_text)
            if 'unknown_observation' in record and self.unknown_dispatcher is not None:
                self.unknown_dispatcher.submit(record['unknown_observation'])
        else:
            record = self.store.load(request['response_path'])
            self.store.validate_prepared(record)
        if audience == 'machine' and operation in ('capture', 'hook', 'view'): return record
        if operation in ('capture', 'hook', 'view'):
            result = navigation(record, request.get('view'))
            result['run_id'] = record['run_id']
            mode = request.get('view', 'brief')
            if mode == 'full':
                result['full_path'] = record['full_path']
            else:
                result['preview'] = (record['_prepared']['brief'] if mode == 'brief'
                                     else self.store.expanded(record))
            require_budget(result, self.policy)
            return result
        return inspect_source(record, self.store.source(record, request['channel']), request, self.policy,
                              audience=audience)

    def prepare(self, record, sources, audience, selected_parser=None, request=None):
        from ..modules.result_views.unknown_inputs import generic_profile, UnknownObservation
        from ..modules.result_views.tool_results import ToolResultFailure
        try:
            pair = prepare_views(record, sources, self.policy, selected_parser=selected_parser)
        except ToolResultFailure as error:
            if error.code != 'unmapped_input' or error.field != 'format': raise
            selected_parser = generic_profile('unknown_format')
            pair = prepare_views(record, sources, self.policy, selected_parser=selected_parser)
        if selected_parser and selected_parser.get('reason') in ('unknown_tool', 'unknown_format'):
            record['unknown_observation'] = UnknownObservation.build(
                record, sources['response'], request, selected_parser['reason'], self.policy).data
        if audience == 'agent':
            candidate = navigation(record)
            candidate['run_id'] = record['run_id']
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

