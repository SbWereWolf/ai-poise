"""Delivery intent and state, independent of storage and filesystem effects."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from ..artifact_factory.domain import exact
from ..foundation.errors import DomainError


def text(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise DomainError(f'Explicit {label} required')
    return value


def absolute(value, label):
    text(value, label)
    if not value.startswith('/') or any(p in ('.', '..') for p in value.split('/')):
        raise DomainError(f'{label} must be an absolute canonical path')
    return value


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class DeliveryAgreement:
    data: dict

    @classmethod
    def parse(cls, value):
        exact(value, {'action', 'request_id', 'task_id', 'expected_version', 'authorization', 'declaration'}, 'delivery agreement fields')
        if value['action'] != 'agree':
            raise DomainError('Delivery agreement requires the agree action')
        for key in ('request_id', 'task_id', 'authorization'):
            text(value[key], key)
        if type(value['expected_version']) is not int or value['expected_version'] < 0:
            raise DomainError('Delivery expected version must be a nonnegative integer')
        declaration = value['declaration']
        exact(declaration, {'disposition', 'outputs'}, 'delivery declaration')
        if declaration['disposition'] not in ('deliver', 'no_result'):
            raise DomainError('Unknown delivery disposition')
        outputs = declaration['outputs']
        if not isinstance(outputs, list) or bool(outputs) != (declaration['disposition'] == 'deliver'):
            raise DomainError('Delivery disposition must match explicit outputs')
        ids = set()
        for item in outputs:
            if not isinstance(item, dict):
                raise DomainError('Delivery output must be an object')
            kind = item.get('kind')
            if kind == 'git':
                exact(item, {'id', 'kind', 'repository', 'target_ref', 'commit'}, 'Git delivery')
                absolute(item['repository'], 'repository')
                text(item['target_ref'], 'target_ref')
                if not isinstance(item['commit'], str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', item['commit']):
                    raise DomainError('Exact Git commit required')
            elif kind in ('file', 'ignored_configuration', 'customer'):
                fields = {'id', 'kind', 'source', 'destination', 'digest'}
                if kind == 'customer':
                    fields.add('acknowledgement')
                exact(item, fields, 'file delivery')
                absolute(item['source'], 'source')
                absolute(item['destination'], 'destination')
                if not isinstance(item['digest'], str) or not re.fullmatch('[0-9a-f]{64}', item['digest']):
                    raise DomainError('Delivery digest must be SHA256')
                if kind == 'customer':
                    exact(item['acknowledgement'], {'path', 'receiver'}, 'acknowledgement')
                    absolute(item['acknowledgement']['path'], 'acknowledgement path')
                    text(item['acknowledgement']['receiver'], 'receiver')
            else:
                raise DomainError('Unknown delivery output kind')
            output_id = text(item['id'], 'output id')
            if output_id in ids:
                raise DomainError('Delivery output ids must be unique')
            ids.add(output_id)
        return cls(deepcopy(value))


@dataclass(frozen=True)
class DeliveryState:
    data: dict

    @classmethod
    def agreed(cls, agreement, root, root_identity, epoch):
        return cls({'schema': 'task-delivery-1', 'revision': 0, 'task': agreement.data['task_id'],
                    'status': 'delivery_agreed', 'agreement': deepcopy(agreement.data),
                    'intent_digest': identity(agreement.data), 'root': root,
                    'root_identity': root_identity, 'epoch': epoch, 'confirmed': [],
                    'retirement_started': False, 'retired_artifacts': []})

    @classmethod
    def restore(cls, value):
        required = {'schema', 'revision', 'task', 'status', 'agreement', 'intent_digest',
                    'root', 'root_identity', 'epoch', 'confirmed', 'retirement_started', 'retired_artifacts'}
        if not isinstance(value, dict) or set(value) not in (required, required | {'reason'}):
            raise DomainError('Invalid saved delivery state fields')
        if value['schema'] != 'task-delivery-1':
            raise DomainError('Invalid saved delivery state schema')
        if any(type(value[k]) is not int or value[k] < 0 for k in ('revision', 'epoch')):
            raise DomainError('Invalid saved delivery revision/epoch')
        DeliveryAgreement.parse(value['agreement'])
        if value['task'] != value['agreement']['task_id'] or value['intent_digest'] != identity(value['agreement']):
            raise DomainError('Saved delivery identity changed')
        absolute(value['root'], 'saved Task root')
        if (not isinstance(value['root_identity'], list) or len(value['root_identity']) != 2
                or any(type(v) is not int or v < 0 for v in value['root_identity'])):
            raise DomainError('Invalid saved Task root identity')
        if value['status'] not in {'delivery_agreed', 'delivery_pending', 'delivery_blocked',
                                   'delivery_partial', 'delivery_complete'}:
            raise DomainError('Invalid saved delivery status')
        if type(value['retirement_started']) is not bool:
            raise DomainError('Invalid saved retirement permission')
        if value['status'] in {'delivery_partial', 'delivery_complete'} and not value['retirement_started']:
            raise DomainError('Saved delivery completion lacks retirement permission')
        if (not isinstance(value['retired_artifacts'], list)
                or any(not isinstance(v, str) or not v for v in value['retired_artifacts'])
                or len(value['retired_artifacts']) != len(set(value['retired_artifacts']))):
            raise DomainError('Invalid retired artifact identities')
        if not isinstance(value['confirmed'], list):
            raise DomainError('Invalid saved delivery confirmations')
        expected = {o['id']: {k: v for k, v in o.items() if k != 'source'}
                    for o in value['agreement']['declaration']['outputs']}
        seen = set()
        for receipt in value['confirmed']:
            if (not isinstance(receipt, dict) or not isinstance(receipt.get('id'), str)
                    or receipt['id'] in seen or expected.get(receipt['id']) != receipt):
                raise DomainError('Saved delivery confirmation changed agreed output')
            seen.add(receipt['id'])
        if value['retirement_started'] and seen != set(expected):
            raise DomainError('Retirement permission lacks every confirmed delivery')
        return cls(deepcopy(value))

    def evolve(self, **changes):
        return DeliveryState.restore({**deepcopy(self.data), **changes, 'revision': self.data['revision'] + 1})

    def result(self, *, replayed=False):
        value = self.data
        result = {'task': value['task'], 'status': value['status'], 'replayed': replayed,
                  'agreement_id': value['agreement']['request_id'],
                  'outputs': deepcopy(value['confirmed']), 'retired_artifacts': value['retired_artifacts']}
        if value.get('reason'):
            result.update(reason=value['reason'], recovery={'operation': 'delivery', 'action': 'settle',
                          'task_id': value['task'], 'reason': value['reason']})
        return result
