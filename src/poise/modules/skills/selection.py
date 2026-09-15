"""Validated AI-poise selection facts; matching is owned only by the C004 router."""
from copy import deepcopy

from ..foundation.errors import DomainError
from ..foundation.validation import validate_exact_keys


def exact(value, keys, label):
    validate_exact_keys(value, keys, label, DomainError)


def strings(value, label, nonempty=False):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(v, str) or not v.strip() or '\0' in v for v in value)
            or len(set(value)) != len(value)):
        raise DomainError(f'{label}: explicit unique strings required')


def path_pattern(value):
    if value.startswith('/') or '\\' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise DomainError(f'Expected relative AI-poise path pattern: {value}')


class SkillSelection:
    def __init__(self, document):
        self._document = deepcopy(document)

    @classmethod
    def parse(cls, raw, catalog):
        exact(raw, {'schema', 'subject', 'facts', 'rules', 'excluded'}, 'skill selection')
        if raw['schema'] != 'ai-poise-skill-selection-1' or raw['subject'] != 'ai-poise':
            raise DomainError('Selection metadata is only for AI-poise development')
        strings(raw['facts'], 'selection facts')
        if not isinstance(raw['rules'], list) or not raw['rules'] or not isinstance(raw['excluded'], list):
            raise DomainError('Explicit selection rules and exclusions required')
        ids, selected, excluded = set(), set(), set()
        for rule in raw['rules']:
            exact(rule, {'id', 'priority', 'when', 'skills'}, 'selection rule')
            strings([rule['id']], 'rule id', True)
            if rule['id'] in ids: raise DomainError('Duplicate selection rule')
            ids.add(rule['id'])
            if type(rule['priority']) is not int or rule['priority'] < 0:
                raise DomainError('Selection priority must be a non-negative integer')
            exact(rule['when'], {'stages', 'paths_any', 'facts_all'}, 'selection condition')
            for key, values in rule['when'].items(): strings(values, key)
            if set(rule['when']['facts_all']) - set(raw['facts']):
                raise DomainError('Unknown selection fact')
            for pattern in rule['when']['paths_any']: path_pattern(pattern)
            strings(rule['skills'], 'selected skills', True)
            catalog.select(rule['skills'])
            selected.update(rule['skills'])
        for item in raw['excluded']:
            exact(item, {'id', 'reason'}, 'skill exclusion')
            strings([item['id'], item['reason']], 'skill exclusion')
            catalog.select([item['id']])
            if item['id'] in excluded: raise DomainError('Duplicate skill exclusion')
            excluded.add(item['id'])
        if selected & excluded: raise DomainError('Selected skill cannot also be excluded')
        if selected | excluded != {s.id for s in catalog.skills}:
            raise DomainError('Every catalog skill must be selected or explicitly excluded')
        canonical = deepcopy(raw)
        canonical['facts'].sort()
        canonical['rules'].sort(key=lambda r: (r['priority'], r['id']))
        for rule in canonical['rules']:
            rule['skills'].sort()
            for values in rule['when'].values(): values.sort()
        canonical['excluded'].sort(key=lambda i: i['id'])
        return cls(canonical)

    def as_dict(self):
        return deepcopy(self._document)
