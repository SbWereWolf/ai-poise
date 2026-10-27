"""One public external result followed by advisory capability observations."""
from domain.generic import Problem, exact_fields, invalid


class HookInterception:
    def __init__(self, definition, sessions, commands, capabilities, files, limits, specs):
        self.definition, self.sessions = definition, sessions
        self.commands, self.capabilities, self.files = commands, capabilities, files
        self.limits, self.specs = limits, specs

    def handle(self, value):
        exact_fields(value, {'workspace', 'request'}, '/input')
        workspace = self.files.checkout(value['workspace'], '/input/workspace')
        if not isinstance(value['request'], dict):
            invalid('/input/request')
        session = self.sessions.status()['session']
        target = self.definition.external
        if target is None:
            raise Problem('external_not_configured', {}, 'configure:external', 'unavailable')
        external, primary = None, None
        try:
            call = self.commands.run(target['argv'], target['cwd'], target['environment'], value['request'],
                                     self.limits['provider_timeout_seconds'],
                                     self.limits['provider_output_bytes'], 'external')
            try:
                response = self.commands.response(call)
            except (ValueError, RecursionError) as error:
                raise Problem('external_response', {'reason': str(error)},
                              'configure:external', 'unavailable') from None
            external = {'exit_code': call['exit_code'], 'response': response}
            if call['exit_code'] != 0:
                primary = Problem('external_unavailable', {'exit_code': call['exit_code']},
                                  'configure:external', 'unavailable')
        except Problem as error:
            primary = error
        except OSError as error:
            primary = Problem('io_unavailable', {'reason': str(error)}, 'configure:files', 'unavailable')
        observations = self.capabilities.observe(self.specs, workspace)
        result = {'session': session, 'external': external, 'capability_checks': observations}
        if primary is not None:
            primary.result = result
            raise primary
        return result
