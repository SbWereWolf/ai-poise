"""Batch reading coordinates the shared intake owners and delivery boundaries."""
from ..modules.document_intake.contracts import ReadFailure
from ..modules.document_intake.ports import ContentSource, ReadBudget


class ContentReader:
    def __init__(self, configuration, source: ContentSource, sessions, resources, budget: ReadBudget, *, command=None):
        self.configuration, self.source, self.sessions, self.resources = configuration, source, sessions, resources
        self.budget = budget
        self.command = command

    @staticmethod
    def _outcome(result):
        failures = [i for i in result['items'] if i['status'] == 'failed']
        if not failures:
            return 0
        result['status'] = 'failed' if len(failures) == len(result['items']) else 'partial'
        # Item failures are owned by their frames, not the packet-level error.
        result['error'] = None
        return 1 if result['status'] == 'partial' else ReadFailure(failures[0]['error']['code']).exit

    def execute(self, request, representation, result):
        op = request['operation']
        if op == 'coverage':
            if self.command is None:
                raise ReadFailure('invalid_policy', field='command_intake.policy')
            result['context'], result['details'] = self.command.coverage()
            return 0, None
        if op == 'prepare_template':
            result['details'] = self.sessions.prepare_template()
            return 0, None
        r = self.resources
        if op == 'recover':
            result['status'], result['details'] = r.recover()
            return 0, None
        observation = r.observe_active()
        prepared = []
        general_versions = {}
        if op == 'read':
            for item in request['reads']:
                try:
                    if self.command is not None:
                        from ..modules.document_intake.command import CommandFailure
                        try:
                            general_versions[item['key']] = self.command.source.direct_version(item['path'])
                        except (OSError, CommandFailure):
                            pass
                    prepared.append(self.source.select(self.configuration.bound.checkout, item, self.configuration.policy))
                except ReadFailure as exc:
                    prepared.append(exc)
        with r.store.locked():
            r.admit(observation, adopt=op == 'adopt')
            result['context'], result['summary'] = self.sessions.context()
            if op == 'advance_context':
                generation = r.registry.advance_context(r.scope, request['request_id'], request['reason'])
                result['context'], result['summary'] = self.sessions.context()
                result['context']['generation'] = generation
                result['details'] = dict(request_id=request['request_id'], reason=request['reason'])
            elif op in ('prepare', 'adopt'):
                result['details'] = dict(path=str(r.target), registry_path=str(r.registry.path), episode=r.scope.episode)
            if op != 'read':
                return 0, None
            path = r.reserve_response()
            result['response_path'] = str(path)
            generation, result['items'], result['summary'] = r.registry.preview_batch(r.scope, prepared)
            result['context']['generation'] = generation
            self._outcome(result)
            size, lines = self.budget.measure(result, prepared, representation)
            limits = self.configuration.policy['output']
            if size > limits['max_bytes'] or lines > limits['max_lines']:
                result.update(items=[], summary=self.sessions.context()[1], response_path=None)
                raise ReadFailure('output_limit')
            result['items'], result['error'], result['status'] = [], None, 'ready'
            for value in prepared:
                if isinstance(value, ReadFailure):
                    item = dict(key=value.key, status='failed', error=value.as_dict())
                else:
                    try:
                        general = None
                        if self.command is not None and value.key in general_versions:
                            general = self.command.source.direct_plan(value, generation, general_versions[value.key])
                            if not self.command.source.current(general):
                                general = None
                                result['details'] = dict(coverage_unconfirmed=True)
                        item = r.registry.register(r.scope, value, generation, general_plan=general)
                    except ReadFailure as exc:
                        item = dict(key=value.key, status='failed', error=exc.as_dict())
                result['items'].append(item)
            result['context'], result['summary'] = self.sessions.context()
            code = self._outcome(result)
            try:
                saved = r.save_response(path, result)
            except (OSError, ReadFailure) as exc:
                result['response_path'] = None
                raise ReadFailure('result_save_failed') from exc
            return code, saved
