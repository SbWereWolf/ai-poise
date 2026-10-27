"""Generic capability observations, not Session or Task admission."""
from domain.generic import Problem, exact_fields, invalid
from domain.probes import validate_probe


class CapabilityChecks:
    def __init__(self, executor, files, limits):
        self.executor, self.files, self.limits = executor, files, limits

    def run(self, request):
        exact_fields(request, {'workspace', 'probes'}, '/input')
        workspace = self.files.checkout(request['workspace'], '/input/workspace')
        values = request['probes']
        if not isinstance(values, list):
            invalid('/input/probes')
        if len(values) > self.limits['batch_count']:
            raise Problem('batch_limit', {'limit': 'batch_count'}, 'reduce:batch')
        specs = [validate_probe(value, '/input/probes/' + str(index), self.limits)
                 for index, value in enumerate(values)]
        if len({value.data['id'] for value in specs}) != len(specs):
            invalid('/input/probes')
        observations = [self.executor.observe(spec, workspace) for spec in specs]
        missing = [spec.data['id'] for spec, item in zip(specs, observations)
                   if spec.data['required'] and item['status'] != 'available']
        result = {'ready': not missing, 'missing_required': missing, 'observations': observations}
        if missing:
            raise Problem('capabilities_unavailable', {'ids': missing}, 'configure:capability',
                          'unavailable', result)
        return result
