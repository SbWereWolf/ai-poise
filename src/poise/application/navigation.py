"""One read-only navigation composition over the existing probe executor."""
from ..modules.capabilities.navigation import parse_navigation, ordered_providers, bound_probe
from ..modules.foundation.errors import PoiseError


class CodeNavigation:
    def __init__(self, executor, files):
        self.executor, self.files = executor, files

    def run(self, request):
        req = parse_navigation(request)
        checkout = self.files.checkout(req['checkout'])
        before = self.files.snapshot(checkout, req['files'])
        # Validate every augmented probe before the first external effect.
        specs = {p['id']: bound_probe(p, before) for p in req['providers']}
        observations = []
        result = {'status': 'unavailable', 'provider': None, 'checkout': checkout,
                  'scope': sorted(before), 'observations': observations,
                  'limitation': 'Proof covers only declared files, not the entire index or live IDE installation.'}
        for provider in ordered_providers(req['providers'], req['operation']):
            observed = self.executor.observe(specs[provider['id']], checkout)
            observations.append({'provider': provider['id'], **observed})
            try:
                changed = self.files.snapshot(checkout, req['files']) != before
            except PoiseError:
                changed = True
            if changed:
                return {**result, 'status': 'inconclusive', 'reason': 'inspected files changed during observation'}
            if observed['status'] == 'available':
                return {**result, 'status': 'proved', 'provider': provider['id'],
                        'receipt': observed['receipt']}
        return {**result, 'reason': 'No applicable provider proved the operation with exact identity and indexed bytes'}
