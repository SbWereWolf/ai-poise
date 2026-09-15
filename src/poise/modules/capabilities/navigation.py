"""Navigation admission belongs to capability policy, not a second IDE/index owner."""
from copy import deepcopy

from .domain import ProbeSpec, nonempty
from ..artifact_factory.domain import exact
from ..foundation.errors import PoiseError

OPERATIONS = frozenset({'symbol', 'usages', 'hierarchy', 'inspection', 'documentation', 'structural'})
AST_OPERATIONS = frozenset({'symbol', 'usages', 'hierarchy', 'structural'})


def _pointer(value, name):
    if not isinstance(value, list) or not value:
        raise PoiseError(f'{name}: explicit JSON key path required')
    for key in value: nonempty(key, name)


def parse_navigation(request):
    exact(request, {'checkout', 'operation', 'files', 'providers'}, 'navigation')
    nonempty(request['checkout'], 'checkout')
    if not isinstance(request['operation'], str) or request['operation'] not in OPERATIONS:
        raise PoiseError('Unsupported read-only navigation operation')
    if (not isinstance(request['files'], list) or not request['files']
            or any(not isinstance(v, str) or not v for v in request['files'])
            or len(set(request['files'])) != len(request['files'])):
        raise PoiseError('Explicit unique representative file paths required')
    if not isinstance(request['providers'], list) or len(request['providers']) > 20:
        raise PoiseError('At most 20 explicitly configured providers are supported')
    ids = set()
    for provider in request['providers']:
        exact(provider, {'id', 'kind', 'operations', 'identity_path', 'digests_path', 'probe'}, 'navigation provider')
        nonempty(provider['id'], 'provider id')
        if provider['id'] in ids: raise PoiseError('Duplicate navigation provider ID')
        ids.add(provider['id'])
        if provider['kind'] not in ('ide', 'ast'): raise PoiseError('Provider must be ide or ast')
        operations = provider['operations']
        if (not isinstance(operations, list) or not operations
                or any(not isinstance(v, str) or v not in OPERATIONS for v in operations)
                or len(set(operations)) != len(operations)):
            raise PoiseError('Explicit unique provider operations required')
        for key in ('identity_path', 'digests_path'): _pointer(provider[key], key)
        left, right = provider['identity_path'], provider['digests_path']
        if left[:len(right)] == right or right[:len(left)] == left:
            raise PoiseError('Identity and indexed-content assertions must not overlap')
        ProbeSpec.parse(provider['probe'])
    return deepcopy(request)


def ordered_providers(providers, operation):
    """Stable caller order within each kind; AST is direct for structural work."""
    kinds = ('ast',) if operation == 'structural' else ('ide', 'ast')
    return [p for kind in kinds for p in providers if p['kind'] == kind
            and operation in p['operations']
            and (kind != 'ast' or operation in AST_OPERATIONS)]


def bound_probe(provider, digests):
    raw = deepcopy(provider['probe'])
    raw['project_bound'] = True
    raw['json_assertions'] += [
        {'path': provider['identity_path'], 'equals': '${workspace}'},
        {'path': provider['digests_path'], 'equals': digests},
    ]
    return ProbeSpec.parse(raw)
