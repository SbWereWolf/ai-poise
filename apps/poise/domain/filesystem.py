"""Stateless declared-root decisions and narrow file-write arguments."""
from dataclasses import dataclass
from pathlib import Path

from domain.generic import Problem, exact_fields, invalid, text


class PolicyIssue(Exception):
    def __init__(self, code, field, effect_index=None):
        super().__init__(code)
        self.code, self.field, self.effect_index = code, field, effect_index

    def view(self):
        return {'code': self.code, 'field': self.field, 'effect_index': self.effect_index}


@dataclass(frozen=True)
class Root:
    id: str
    path: Path
    read: bool
    write: bool


@dataclass(frozen=True)
class Effect:
    index: int
    operation: str
    path: Path


def _fields(value, fields, field, code, index=None, *, exact=False):
    if not isinstance(value, dict):
        raise PolicyIssue(code, field, index)
    missing = sorted(fields - value.keys())
    if missing:
        raise PolicyIssue(code, field + '/' + missing[0], index)
    if exact and value.keys() - fields:
        raise PolicyIssue(code, field, index)
    return value


def _text(value, field, code, index=None):
    if not isinstance(value, str) or not value or '\0' in value:
        raise PolicyIssue(code, field, index)
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise PolicyIssue(code, field, index) from None
    return value


def _schema(value, expected, field, code):
    if not isinstance(value, str):
        raise PolicyIssue(code, field)
    if value != expected:
        raise PolicyIssue('unsupported_schema', field)


def parse_policy(value):
    _fields(value, {'schema', 'roots'}, '/policy', 'invalid_policy')
    _schema(value['schema'], 'poise-fs-policy/v1', '/policy/schema', 'invalid_policy')
    if not isinstance(value['roots'], list):
        raise PolicyIssue('invalid_policy', '/policy/roots')
    identities = set()
    for index, root in enumerate(value['roots']):
        field = '/policy/roots/' + str(index)
        _fields(root, {'id', 'path', 'read', 'write'}, field, 'invalid_policy')
        identity = _text(root['id'], field + '/id', 'invalid_policy')
        _text(root['path'], field + '/path', 'invalid_policy')
        if identity in identities:
            raise PolicyIssue('invalid_policy', field + '/id')
        identities.add(identity)
        for operation in ('read', 'write'):
            if type(root[operation]) is not bool:
                raise PolicyIssue('invalid_policy', field + '/' + operation)
    return value['roots']


def parse_request(value):
    _fields(value, {'schema', 'base', 'effects'}, '/request', 'invalid_request', exact=True)
    _schema(value['schema'], 'poise-fs-request/v1', '/request/schema', 'invalid_request')
    _text(value['base'], '/request/base', 'invalid_request')
    if not isinstance(value['effects'], list) or not value['effects']:
        raise PolicyIssue('invalid_request', '/request/effects')
    for index, effect in enumerate(value['effects']):
        field = '/request/effects/' + str(index)
        _fields(effect, {'operation', 'path'}, field, 'invalid_request', index, exact=True)
        if effect['operation'] not in ('read', 'write'):
            raise PolicyIssue('invalid_request', field + '/operation', index)
        _text(effect['path'], field + '/path', 'invalid_request', index)
    return value['base'], value['effects']


def decide(roots, effects):
    items = []
    for effect in effects:
        matching = [root for root in roots if effect.path.is_relative_to(root.path)]
        permitted = all(root.read if effect.operation == 'read' else root.write for root in matching)
        decision = 'outside' if not matching else 'allow' if permitted else 'deny'
        items.append({'index': effect.index, 'operation': effect.operation, 'path': str(effect.path),
                      'roots': sorted(root.id for root in matching), 'decision': decision})
    decisions = [item['decision'] for item in items]
    decision = 'deny' if 'deny' in decisions else 'outside' if all(
        item == 'outside' for item in decisions) else 'allow'
    return {'schema': 'poise-fs-result/v1', 'status': 'evaluated', 'decision': decision,
            'effects': items, 'error': None}


def rejected(issue):
    return {'schema': 'poise-fs-result/v1', 'status': 'rejected', 'decision': None,
            'effects': [], 'error': issue.view()}


def parse_writes(value):
    exact_fields(value, {'schema', 'base', 'writes'}, '/input')
    if not isinstance(value['schema'], str):
        invalid('/input/schema')
    if value['schema'] != 'poise-write-request/v1':
        raise Problem('unsupported_schema', {'schema': value['schema']}, 'select:schema')
    if not isinstance(value['writes'], list) or not value['writes']:
        invalid('/input/writes')
    items = []
    for index, item in enumerate(value['writes']):
        field = '/input/writes/' + str(index)
        exact_fields(item, {'path', 'text'}, field)
        path = text(item['path'], field + '/path')
        if not isinstance(item['text'], str):
            invalid(field + '/text')
        try:
            data = item['text'].encode('utf-8')
        except UnicodeError:
            invalid(field + '/text')
        items.append((path, data))
    # A present base value belongs exclusively to common request validation.
    return value['base'], items
