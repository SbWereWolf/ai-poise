"""Read explicit collection policy without rewriting operator configuration."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...modules.accounting.delivery import TelemetryDeliveryPolicy
from ...modules.accounting.domain import MetricPolicy
from ...modules.accounting.metrics.domain import identity
from ...modules.foundation.errors import PoiseError
from ...modules.foundation.validation import configuration_fields, nonempty
from ..goal_config import read_document


SOURCE_VERSIONS = {
    'codex-native': '1', 'codex-transcript': 'invocations-2.5',
    'hook-adoption': '1', 'skill-policy': '1',
}


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise PoiseError(label + ': positive integer required')
    return value


def paths(value, label):
    if not isinstance(value, list) or not value:
        raise PoiseError(label + ': explicit paths required')
    result = []
    for item in value:
        nonempty(item, label)
        if not Path(item).is_absolute():
            raise PoiseError(label + ': absolute paths required')
        result.append(item)
    if len(result) != len(set(result)):
        raise PoiseError(label + ': duplicate paths')
    return result


def timezone(name):
    nonempty(name, 'metric timezone')
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PoiseError('Unknown metric timezone: ' + name) from exc


@dataclass(frozen=True)
class AgentMetricsPolicy:
    """Effective known options only; unknown extensions never enter provenance."""
    root: Path
    accounting: MetricPolicy
    delivery: TelemetryDeliveryPolicy
    directory: Path
    collection: dict
    effective: dict

    @property
    def digest(self):
        return identity(self.effective)

    @property
    def database(self):
        return self.root / self.accounting.data['storage']['database']

    @property
    def lock(self):
        return self.root / self.accounting.data['storage']['lock']

    @classmethod
    def read(cls, path):
        selected = Path(path)
        if not selected.is_absolute():
            raise PoiseError('Select an absolute agent metrics policy path')
        return cls.parse(read_document(selected))

    @classmethod
    def parse(cls, raw):
        value = configuration_fields(raw, {'schema', 'root', 'accounting', 'delivery',
                                          'collection', 'retention'}, 'agent metrics policy')
        if value['schema'] != 'poise-agent-metrics-policy-1':
            raise PoiseError('Unsupported agent metrics policy schema')
        nonempty(value['root'], 'agent metrics root')
        root = Path(value['root'])
        if not root.is_absolute():
            raise PoiseError('Agent metrics root must be absolute')
        accounting = MetricPolicy.parse(value['accounting'])
        timezone(accounting.data['timezone'])
        delivery = configuration_fields(value['delivery'], {'directory', 'policy'}, 'metric delivery')
        nonempty(delivery['directory'], 'metric delivery directory')
        delivery_policy = TelemetryDeliveryPolicy.parse(delivery['policy'])
        collection = deepcopy(configuration_fields(value['collection'],
            {'max_pending', 'max_source_bytes', 'max_records', 'sources'}, 'metric collection'))
        for key in ('max_pending', 'max_source_bytes', 'max_records'):
            positive(collection[key], 'collection.' + key)
        if not isinstance(collection['sources'], list):
            raise PoiseError('Metric collection sources must be an explicit list')
        sources = []
        for entry in collection['sources']:
            source = configuration_fields(entry, {'id', 'kind', 'parser_version', 'paths'}, 'metric source')
            nonempty(source['id'], 'metric source id')
            kind = source['kind']
            if not isinstance(kind, str) or kind not in SOURCE_VERSIONS:
                raise PoiseError('Unsupported metric source kind')
            if source['parser_version'] != SOURCE_VERSIONS[kind]:
                raise PoiseError('Unsupported parser version for metric source ' + source['id'])
            source['paths'] = paths(source['paths'], 'metric source paths')
            sources.append(source)
        if len({s['id'] for s in sources}) != len(sources):
            raise PoiseError('Metric source ids must be unique')
        collection['sources'] = sources
        retention = configuration_fields(value['retention'], {'mode'}, 'metric retention')
        if retention['mode'] != 'keep':
            raise PoiseError('Unsupported metric retention mode; use keep')
        effective = {'schema': value['schema'], 'root': str(root), 'accounting': accounting.data,
                     'delivery': {'directory': delivery['directory'], 'policy': asdict(delivery_policy)},
                     'collection': collection, 'retention': retention}
        return cls(root, accounting, delivery_policy, root / delivery['directory'],
                   collection, effective)

