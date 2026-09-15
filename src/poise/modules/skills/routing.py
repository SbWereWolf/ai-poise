"""Pure AI-poise route selection, not a Task/check execution owner."""
from copy import deepcopy

from ..foundation.errors import DomainError
from ..foundation.paths import matches_allowed_path
from .selection import exact, path_pattern, strings


class RoutingPolicy:
    def __init__(self, document):
        self.document = deepcopy(document)

    @classmethod
    def parse(cls, raw, selection, package_ids):
        exact(raw, {'schema', 'known_paths', 'handler_facts', 'checks'}, 'development routing')
        if raw['schema'] != 'ai-poise-development-routing-1':
            raise DomainError('Unsupported AI-poise development routing schema')
        strings(raw['known_paths'], 'known paths', True)
        for pattern in raw['known_paths']:
            path_pattern(pattern)
        if not isinstance(raw['handler_facts'], dict) or not raw['handler_facts']:
            raise DomainError('Explicit handler facts required')
        facts = selection.as_dict()['facts']
        for handler, values in raw['handler_facts'].items():
            strings([handler], 'handler')
            strings(values, 'handler facts')
            if set(values) - set(facts):
                raise DomainError('Unknown handler fact')
        if not isinstance(raw['checks'], list):
            raise DomainError('Explicit check-selection declarations required')
        ids = set()
        for check in raw['checks']:
            exact(check, {'id', 'paths_any', 'packages'}, 'check selection')
            strings([check['id']], 'check selection ID')
            if check['id'] in ids:
                raise DomainError('Duplicate check selection ID')
            ids.add(check['id'])
            strings(check['paths_any'], 'check paths', True)
            for pattern in check['paths_any']:
                path_pattern(pattern)
            strings(check['packages'], 'check packages', True)
            if set(check['packages']) - set(package_ids):
                raise DomainError('Unknown registered AI-poise package')
        return cls(raw)


def parse_route(raw):
    exact(raw, {'checkout', 'stage', 'handler', 'scope_paths', 'changed_paths', 'facts',
                'required_methods', 'registered_methods', 'result_contract'}, 'route facts')
    for key in ('checkout', 'stage', 'handler'):
        strings([raw[key]], key, True)
    for key in ('scope_paths', 'changed_paths', 'facts', 'required_methods', 'registered_methods'):
        strings(raw[key], key)
        if len(raw[key]) > 4096:
            raise DomainError(f'{key}: route collection exceeds 4096 items')
    for key in ('scope_paths', 'changed_paths'):
        for path in raw[key]:
            path_pattern(path)
    if not isinstance(raw['result_contract'], dict):
        raise DomainError('Explicit result contract facts required')
    return deepcopy(raw)


def select_route(request, selection, policy):
    """Match declared facts only; never infer technology or execute packages."""
    document = selection.as_dict()
    config = policy.document
    paths = sorted(set(request['scope_paths'] + request['changed_paths']))
    facts = set(request['facts'])
    missing = [f'unknown_fact:{v}' for v in sorted(facts - set(document['facts']))]
    if request['handler'] not in config['handler_facts']:
        missing.append('unmapped_handler:' + request['handler'])
    else:
        facts.update(config['handler_facts'][request['handler']])
    if not paths:
        missing.append('scope_or_changed_paths')
    for path in paths:
        if not any(matches_allowed_path(path, p) for p in config['known_paths']):
            missing.append('unmapped_path:' + path)
    missing.extend('unregistered_method:' + m for m in request['required_methods']
                   if m not in request['registered_methods'])
    selected, matching = [], []
    for rule in document['rules']:
        when = rule['when']
        if when['stages'] and request['stage'] not in when['stages']:
            continue
        if when['paths_any'] and not any(matches_allowed_path(p, pattern)
                for p in paths for pattern in when['paths_any']):
            continue
        if not set(when['facts_all']).issubset(facts):
            continue
        matching.append(rule['id'])
        selected.extend(s for s in rule['skills'] if s not in selected)
    packages = set()
    for rule in config['checks']:
        if any(matches_allowed_path(p, pattern) for p in paths for pattern in rule['paths_any']):
            packages.update(rule['packages'])
    return {'skills': selected, 'matched_selection_rules': matching,
            'facts': sorted(facts), 'missing_inputs': missing,
            'checks': {'required_methods': list(request['required_methods']),
                       'suggested_packages': sorted(packages), 'executed': False}}
