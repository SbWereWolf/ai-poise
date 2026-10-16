"""Strict placement inputs and public diagnostic values; no filesystem access."""
from copy import deepcopy
from dataclasses import dataclass
import re

from ..foundation.errors import DomainError
from ..foundation.validation import configuration_fields, validate_exact_keys


RESULT_SCHEMA = 'poise-placement-result-1'
ITEM_FIELDS = {'key', 'purpose', 'recipient', 'lifetime', 'name', 'path'}
AUTH_FIELDS = {'reference', 'root', 'prefix', 'purposes', 'recipients', 'lifetimes'}
IDENTITY_FIELDS = ('key', 'purpose', 'recipient', 'lifetime', 'name',
                   'root', 'owner', 'path', 'absolute_path')
RECORD_FIELDS = set(IDENTITY_FIELDS) | {'category', 'rule', 'versioning', 'authorization_refs'}


class PlacementInputError(DomainError):
    def __init__(self, code, field, action):
        self.code, self.field, self.action = code, field, action
        super().__init__(f'{code}: {field}; {action}')

    def response(self):
        return {'schema': RESULT_SCHEMA, 'status': 'rejected',
                'error': {'code': self.code, 'field': self.field, 'action': self.action}}


def text(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def strings(value, *, empty=True, ordered=False):
    return (isinstance(value, list) and (empty or bool(value))
            and all(text(v) for v in value) and len(value) == len(set(value))
            and (not ordered or value == sorted(value)))


def relative(value, *, pattern=False, empty=False):
    if empty and value == '':
        return True
    return (text(value) and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and not any(c in value for c in ('\\:' if pattern else '\\:*?[]'))
            and all(p not in ('', '.', '..') for p in value.split('/')))


def basename(value):
    return relative(value) and '/' not in value


def key(value):
    return isinstance(value, str) and value not in ('.', '..') and re.fullmatch(r'[A-Za-z0-9._-]+', value) is not None


def exact(value, fields, code='invalid_request', field='request', action='provide:request'):
    try:
        validate_exact_keys(value, fields, field, DomainError)
    except DomainError as exc:
        raise PlacementInputError(code, field, action) from exc


def configured(value, fields):
    try:
        return configuration_fields(value, fields, 'placement policy', DomainError)
    except DomainError as exc:
        raise PlacementInputError('invalid_policy', 'policy', 'correct:policy') from exc


def authorization_id(value):
    return value['root'], value['prefix'], value['reference']


def authorizations(value):
    if not isinstance(value, list):
        raise PlacementInputError('invalid_request', 'request', 'provide:request')
    unique = {}
    for raw in value:
        exact(raw, AUTH_FIELDS)
        if (not text(raw['reference']) or not text(raw['root']) or not relative(raw['prefix'])
                or any(not strings(raw[k], empty=False) for k in ('purposes', 'recipients', 'lifetimes'))):
            raise PlacementInputError('invalid_request', 'request', 'provide:request')
        current = deepcopy(raw)
        for axis in ('purposes', 'recipients', 'lifetimes'):
            current[axis] = sorted(current[axis])
        identity = authorization_id(current)
        if identity in unique and unique[identity] != current:
            raise PlacementInputError('invalid_request', 'request', 'provide:request')
        unique[identity] = current
    return [unique[k] for k in sorted(unique)]


def scope_matches(grant, root, path, item):
    prefix = grant['prefix']
    return (grant['root'] == root and (path == prefix or path.startswith(prefix + '/'))
            and item['purpose'] in grant['purposes']
            and item['recipient'] in grant['recipients']
            and item['lifetime'] in grant['lifetimes'])


def diagnostic(code, key, field, action):
    return {'key': key, 'code': code, 'field': field, 'action': action}


@dataclass(frozen=True)
class PlacementRequest:
    operation: str
    items: tuple
    authorizations: tuple

    @classmethod
    def parse(cls, value, max_items):
        exact(value, {'operation', 'items', 'authorizations'})
        if (value['operation'] not in ('prepare', 'check')
                or not isinstance(value['items'], list) or not 1 <= len(value['items']) <= max_items):
            raise PlacementInputError('invalid_request', 'request', 'provide:request')
        items, seen = [], set()
        for raw in value['items']:
            exact(raw, ITEM_FIELDS)
            if (not key(raw['key']) or raw['key'] in seen
                    or any(not text(raw[k]) for k in ('purpose', 'recipient', 'lifetime'))):
                raise PlacementInputError('invalid_request', 'request', 'provide:request')
            path = raw['path']
            if (not basename(raw['name'])
                    or (path is None and value['operation'] == 'check')
                    or (path is not None and (not relative(path) or path.split('/')[-1] != raw['name']))):
                raise PlacementInputError('invalid_paths', 'items', 'provide:paths')
            seen.add(raw['key'])
            items.append(deepcopy(raw))
        return cls(value['operation'], tuple(sorted(items, key=lambda v: v['key'])),
                   tuple(authorizations(value['authorizations'])))
