"""C028.5: ingest source observations without synthesizing time or intervals."""
from copy import deepcopy
import json

import pytest

from conftest import write_json
from poise.application.telemetry import OptionalTelemetry
from poise.application.work import WorkTools
from poise.infrastructure.telemetry import AsyncTelemetryDispatcher
from poise.infrastructure.telemetry_spool import TelemetrySpool
from poise.modules.accounting.domain import MetricPolicy, parse_telemetry
from poise.common import PoiseError
from poise.runtime import Poise
from .test_domain import policy
from .test_telemetry_delivery import delivery_policy
from .test_optional_telemetry_isolation import _Clock, _request, _Runtime


def raw(*parameters, observed_at='2026-09-12T19:45:00.012345+05:00'):
    return {'usage': [], 'intervals': [], 'cause': None, 'finding_targets': [],
            'source_metrics': [
                {'source': 'test', 'stream': 'api', 'event_id': f'api-{index}',
                 'observed_at': observed_at, 'parameters': value}
                for index, value in enumerate(parameters)]}


def runtime_for(project):
    project['cfg']['accounting'] = policy()
    write_json(project['config_path'], project['cfg'])
    return Poise(project['config_path'], 'source-metrics', clock=_Clock())


def test_source_metrics_preserve_exact_source_time_and_unknown_fields(project, tmp_path):
    runtime = runtime_for(project)
    source = raw({'duration_ms': 123.75, 'usage': {'tokens': 4}, 'vendor': ['x', None]},
                 {'usage': {}}, {'duration_ms': None})
    expected = deepcopy(source)
    delivered = []
    dispatch = AsyncTelemetryDispatcher(delivered.append, 4)
    capture = OptionalTelemetry(_Clock(), dispatch, 'source-metrics')
    token = capture.begin('show', None, source, 't')
    source['source_metrics'][0]['parameters']['duration_ms'] = 999
    assert capture.finish(token, None, {'status': 'read_only'})
    capture.flush()
    dispatch.close()
    assert len(delivered) == 1
    # Persist a different day from source observation; retry must retain its source bytes.
    first = TelemetrySpool(tmp_path/'spool', delivery_policy(),
        lambda event: (_ for _ in ()).throw(OSError('offline')), now=lambda: 200)
    first(delivered[0])
    resumed = TelemetrySpool(tmp_path/'spool', delivery_policy(),
        runtime.accounting.port.process, now=lambda: 205)
    assert resumed.replay() == {'attempted': 1, 'delivered': 1, 'failed': 0, 'corrupt': 0}
    assert runtime.accounting.port.process(delivered[0]) is False
    snapshot = runtime.accounting.port.telemetry_repo.snapshot()
    assert len(snapshot['telemetry']) == 1
    stored = json.loads(snapshot['telemetry'][0]['data'])
    assert stored['telemetry'] == expected
    assert snapshot['cycles'] == []  # Raw metrics are not fabricated intervals.
    assert snapshot['usage'] == []  # No counter guesses from vendor metadata.
    assert snapshot['tasks'] == {}
    assert snapshot['telemetry'][0]['started_at'] != expected['source_metrics'][0]['observed_at']


def test_late_source_observations_do_not_require_source_time_order(project):
    runtime = runtime_for(project)
    sink = runtime.accounting.port.process
    dispatcher = AsyncTelemetryDispatcher(sink, 4)
    capture = OptionalTelemetry(_Clock(), dispatcher, 'source-metrics')
    for value in [raw({'duration_s': 2}, observed_at='2026-09-13T00:00:00Z'),
                  raw({}, observed_at='2026-09-10T00:00:00Z')]:
        token = capture.begin('show', None, value, None)
        assert capture.finish(token, None, {'status': 'read_only'})
    capture.flush()
    dispatcher.close()
    rows = runtime.accounting.port.telemetry_repo.snapshot()['telemetry']
    assert [json.loads(row['data'])['telemetry']['source_metrics'][0]['observed_at']
            for row in rows] == ['2026-09-13T00:00:00Z', '2026-09-10T00:00:00Z']


@pytest.mark.parametrize('change', [
    {'observed_at': None}, {'observed_at': ''}, {'observed_at': '2026-09-12T10:00:00'},
    {'observed_at': 'invalid'}, {'source': 'unconfigured'},
    {'parameters': []}, {'parameters': {'duration': float('nan')}},
    {'parameters': {'duration': float('inf')}}, {'event_id': ''},
])
def test_invalid_source_metric_is_rejected_before_optional_persistence(change):
    value = raw({})
    value['source_metrics'][0].update(change)
    with pytest.raises(PoiseError):
        parse_telemetry(value, MetricPolicy.parse(policy()))


def test_metric_batch_is_bounded_and_owns_immutable_inputs():
    config = policy()
    config['max_events'] = 1
    value = raw({'duration': 0})
    parsed = parse_telemetry(value, MetricPolicy.parse(config))
    value['source_metrics'][0]['parameters']['duration'] = 9
    assert parsed['source_metrics'][0]['parameters'] == {'duration': 0}
    with pytest.raises(PoiseError):
        parse_telemetry(raw({}, {}), MetricPolicy.parse(config))


def test_bad_optional_metric_does_not_reject_valid_work(project):
    runtime = runtime_for(project)
    dispatcher = AsyncTelemetryDispatcher(runtime.accounting.port.process, 4)
    capture = OptionalTelemetry(_Clock(), dispatcher, 'source-metrics')
    packet = _request()
    packet['telemetry'] = raw({}, observed_at='bad source time')
    result = WorkTools(_Runtime(capture)).invoke(packet)
    assert result['results'][0]['value'] == {'state': 'authoritative'}
    dispatcher.flush()
    dispatcher.close()
    assert capture.summary()['failed'] == 1
    assert runtime.accounting.port.telemetry_repo.snapshot()['telemetry'] == []
