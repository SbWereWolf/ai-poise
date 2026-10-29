"""Measurements of one committed source capture, separate from parser chunks."""
import hashlib

from .domain import ObservationFacts, instant
from .tools import text


def capture_health(records, scope, source, descriptor, job, processed):
    started = job.data['started']
    facts = ObservationFacts(scope, source, started['audit_utc'])
    entity = job.identity
    native = job.data['agent_metrics']
    turn = native.get('turn_id') or next((record.event['payload'].get('turn_id') for record in reversed(records)
                                         if record.event['payload'].get('turn_id')), None)
    context = {'job_sha256': entity, 'source_id': source['id'],
               'session_id': scope['session'], 'turn_id': turn}
    for name, value in (('processed', 1), ('failed', 0), ('skipped', 0)):
        facts.add('delivery-health', entity, name, value, 'count', kind='observed', state='known', context=context)
    if started['comparison_domain'] == processed['comparison_domain']:
        latency = (processed['monotonic_ns'] - started['monotonic_ns']) / 1000000
    else:
        latency = (instant(processed['audit_utc']) - instant(started['audit_utc'])).total_seconds() * 1000
    valid = latency >= 0
    facts.add('delivery-health', entity, 'latency_ms', latency if valid else None, 'milliseconds',
              kind='observed', state='known' if valid else 'unknown', context=context,
              reason=None if valid else 'Capture and processing clocks are not comparable')
    message = None
    for record in records:
        payload = record.event['payload']
        if payload.get('type') == 'agent_message':
            message = text(payload.get('message'))
        elif record.event.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'assistant':
            message = text(payload.get('content'))
    measured_message = native.get('native_message')
    if measured_message is None:
        measured_message = {'bytes': None if message is None else len(message.encode()),
                            'sha256': None if message is None else hashlib.sha256(message.encode()).hexdigest()}
    facts.describe('delivery-health', entity, {
        **context, 'status': 'processed', 'error': None, 'source_size': descriptor['boundary'],
        'source_identity': descriptor['source_identity'], 'observed_at': started['audit_utc'],
        'processed_at': processed['audit_utc'],
        'last_assistant_message_bytes': measured_message['bytes'],
        'last_assistant_message_sha256': measured_message['sha256'],
    })
    return facts.export()
