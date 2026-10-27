"""Compose the pure navigation policy with actual external observations."""
from domain.generic import Problem
from domain.probes import ordered_providers, parse_navigation


class CodeNavigation:
    def __init__(self, executor, files, limits):
        self.executor, self.files, self.limits = executor, files, limits

    def run(self, request):
        selected = parse_navigation(request, self.limits)
        checkout = self.files.checkout(selected['checkout'])
        result = {'status': 'unavailable', 'provider': None, 'checkout': checkout,
                  'scope': selected['scope'], 'observations': [],
                  'limitation': 'The result covers this configured operation and declared project scope.'}
        for provider, probe in ordered_providers(selected):
            observed = self.executor.observe(probe, checkout)
            result['observations'].append({'provider': provider['id'], **observed})
            if observed['status'] == 'available':
                return {**result, 'status': 'proved', 'provider': provider['id'],
                        'receipt': observed['receipt']}
        raise Problem('navigation_unavailable', {'operation': selected['operation']},
                      'configure:provider', 'unavailable', result)
