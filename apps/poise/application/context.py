"""Own explicit context with optional public external composition."""
from domain.generic import Problem, argv, environment, exact_fields, invalid, string_list


class GenericContext:
    def __init__(self, installation, files, commands, limits):
        self.installation, self.files, self.commands = installation, files, commands
        self.limits = limits

    def read(self, request):
        exact_fields(request, {'checkout', 'facts', 'external'}, '/input')
        checkout = self.files.checkout(request['checkout'])
        facts = string_list(request['facts'], '/input/facts')
        external = request['external']
        if external is not None:
            exact_fields(external, {'argv', 'input', 'cwd', 'environment'}, '/input/external')
            argv(external['argv'], '/input/external/argv')
            environment(external['environment'], '/input/external/environment')
            self.files.checkout(external['cwd'], '/input/external/cwd')
            if not isinstance(external['input'], dict):
                invalid('/input/external/input')
        result = {'installation_source': self.installation, 'checkout': checkout,
                  'facts': facts, 'external': None}
        if external is None:
            return result
        try:
            call = self.commands.run(external['argv'], external['cwd'], external['environment'],
                                     external['input'], self.limits['provider_timeout_seconds'],
                                     self.limits['provider_output_bytes'], 'external')
            response = self.commands.response(call)
            result['external'] = {'exit_code': call['exit_code'], 'response': response}
            if call['exit_code'] != 0:
                raise Problem('external_unavailable', {'exit_code': call['exit_code']},
                              'configure:external', 'unavailable', result)
        except Problem as exc:
            exc.result = result
            raise
        except ValueError as exc:
            raise Problem('external_response', {'reason': str(exc)}, 'configure:external',
                          'unavailable', result) from exc
        return result
