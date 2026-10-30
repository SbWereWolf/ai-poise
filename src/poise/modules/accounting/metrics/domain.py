"""Pure metric identity, knowledge and public query contracts."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from ..domain import canonical, identity
from ...foundation.errors import PoiseError
from ...foundation.validation import nonempty, validate_exact_keys


FAMILIES = frozenset((
    'usage-current', 'invocation-provenance', 'turn-timing-context', 'turn-counts',
    'tools', 'files-skills-checks', 'hook-adoption', 'delivery-health',
    'optional-task-policy', 'skill-selection',
))
OBSERVATION_SCHEMA = 'poise-agent-metrics-observation-1'


def observation_document(value):
    if not isinstance(value, dict) or value.get('schema') != OBSERVATION_SCHEMA:
        raise PoiseError('Unsupported saved metric observation schema; preserve the store and select its matching producer version')
    return value


def instant(value):
    if not isinstance(value, str):
        raise PoiseError('Source time requires an ISO datetime with a timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PoiseError('Source time requires an ISO datetime with a timezone') from exc
    if result.tzinfo is None:
        raise PoiseError('Source time requires an explicit timezone')
    return result.astimezone(timezone.utc)


def known_counter(value):
    return type(value) is int and value >= 0


class MetricFailure(PoiseError):
    def __init__(self, status, reason, continuation):
        super().__init__(reason)
        self.status, self.reason, self.continuation = status, reason, continuation


@dataclass(frozen=True)
class MetricRequest:
    operation: str
    scope: dict
    period: dict | None
    view: dict | None

    @classmethod
    def parse(cls, value, project):
        if not isinstance(value, dict):
            raise PoiseError('Metric request must be an object')
        operation = value.get('operation')
        if operation not in ('query', 'status', 'replay'):
            raise PoiseError('Select query, status or replay')
        keys = {'schema', 'operation', 'scope'}
        if operation == 'query':
            keys |= {'period', 'view'}
        validate_exact_keys(value, keys, 'metric request', PoiseError)
        if value['schema'] != 'poise-agent-metrics-request-1':
            raise PoiseError('Unsupported metric request schema')
        scope = value['scope']
        validate_exact_keys(scope, {'kind', 'id'}, 'metric scope', PoiseError)
        if scope['kind'] not in ('project', 'session', 'source', 'task', 'sprint'):
            raise PoiseError('Unsupported metric scope')
        nonempty(scope['id'], 'metric scope id')
        if scope['kind'] == 'project' and scope['id'] != project:
            raise PoiseError('Metric scope belongs to another project; select the bound project')
        if operation != 'query':
            if scope != {'kind': 'project', 'id': project}:
                raise PoiseError('Metric mutation/delivery scope must be the bound project')
            return cls(operation, deepcopy(scope), None, None)
        period, view = value['period'], value['view']
        validate_exact_keys(period, {'from', 'to'}, 'metric period', PoiseError)
        times = {key: None if text is None else instant(text) for key, text in period.items()}
        if times['from'] is not None and times['to'] is not None and times['from'] > times['to']:
            raise PoiseError('Metric period ends before it starts')
        if not isinstance(view, dict):
            raise PoiseError('Metric view must be an object')
        validate_exact_keys(view, {'kind', 'family'}, 'metric view', PoiseError)
        if view['kind'] != 'facts':
            raise PoiseError('Select recorded facts; analytical processing belongs to an external consumer')
        if view['family'] is not None and (not isinstance(view['family'], str) or view['family'] not in FAMILIES):
            raise PoiseError('Unsupported metric family')
        return cls(operation, deepcopy(scope), deepcopy(period), deepcopy(view))


class ObservationFacts:
    """Build scalar facts and their entity metadata without any storage dependency."""

    def __init__(self, scope, source, at):
        self.scope, self.source, self.at = deepcopy(scope), deepcopy(source), at
        self.facts, self.metadata = [], []

    def add(self, family, entity, name, value, unit, *, kind, state, context, reason=None):
        nonempty(name, 'metric name')
        nonempty(unit, 'metric unit')
        if family not in FAMILIES or kind not in ('observed', 'derived', 'proxy'):
            raise PoiseError('Invalid metric family or measurement kind')
        if state not in ('known', 'unknown', 'conflict', 'partial', 'unavailable', 'not_applicable'):
            raise PoiseError('Invalid metric availability')
        if state != 'known' and not reason:
            raise PoiseError('Unavailable or partial metric requires its actual reason')
        if state == 'known' and value is None:
            raise PoiseError('A known measurement requires an observed value')
        if state not in ('known', 'partial') and value is not None:
            raise PoiseError('An unavailable measurement cannot contain an invented value')
        if type(value) is bool and unit != 'boolean':
            raise PoiseError('A boolean observation cannot be used as a numeric measurement')
        if value is not None and (type(value) not in (int, float, bool) or
                                  type(value) is float and not math.isfinite(value)):
            raise PoiseError('A metric value must be a finite scalar or null')
        self.facts.append({
            'key': identity([family, entity, name, context]), 'family': family,
            'entity': entity, 'name': name, 'value': value, 'unit': unit, 'kind': kind,
            'availability': {'state': state, 'reason': reason}, 'context': deepcopy(context),
            'scope': deepcopy(self.scope), 'source': deepcopy(self.source), 'at': self.at,
        })

    def describe(self, family, entity, fields):
        if family not in FAMILIES:
            raise PoiseError('Invalid metric metadata family')
        self.metadata.append({'key': identity([family, entity]), 'family': family,
                              'entity': entity, 'fields': deepcopy(fields),
                              'scope': deepcopy(self.scope), 'source': deepcopy(self.source), 'at': self.at})

    def export(self):
        return {'facts': deepcopy(self.facts), 'metadata': deepcopy(self.metadata)}
