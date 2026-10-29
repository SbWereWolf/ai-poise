"""Response identity and conservative usage reconciliation, independent of storage."""
from .domain import canonical, identity, known_counter


COMPONENTS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
              'output_tokens', 'reasoning_output_tokens', 'total_tokens')
PARSER_VERSION = 'invocations-2.0'
CLASSIFICATION_VERSION = 'purpose-1'


def usage_values(observations):
    """Reconcile each observed component; missing and conflicting are not zero."""
    result = {}
    for name in COMPONENTS:
        values = {canonical(item[name]): item[name] for item in observations
                  if isinstance(item, dict) and item.get(name) is not None}
        if len(values) > 1:
            value, state, reason = None, 'conflict', 'Different observed values for the same response component'
        elif values and known_counter(next(iter(values.values()))):
            value, state, reason = next(iter(values.values())), 'known', None
        else:
            value, state, reason = None, 'unknown', 'No valid observed response component'
        result[name] = {'value': value, 'state': state, 'reason': reason}
    # A consistent component remains useful even if another one conflicts.
    def known(name):
        return result[name]['state'] == 'known'
    def invalidate(names, reason):
        for name in names:
            if known(name):
                result[name] = {'value': None, 'state': 'unknown', 'reason': reason}
    if all(known(name) for name in ('input_tokens', 'output_tokens', 'total_tokens')) and (
            result['input_tokens']['value'] + result['output_tokens']['value'] != result['total_tokens']['value']):
        invalidate(('total_tokens',), 'Observed total is inconsistent with input and output')
    for subset, whole in (('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')):
        if known(subset) and known(whole) and result[subset]['value'] > result[whole]['value']:
            invalidate((subset,), 'Observed detail exceeds its containing token count')
    return result


def modern_invocations(records, session):
    groups = {}
    for record in records:
        event = record.event
        if event.get('type') != 'token_usage_record':
            continue
        payload = event['payload']
        response = payload.get('response_id')
        valid = isinstance(response, str) and bool(response.strip()) and payload.get('thread_id') == session
        key = 'response:' + identity([session, response]) if valid else 'unknown:' + identity([session, record.event_hash])
        groups.setdefault(key, []).append(record)
    result = []
    for key, observations in groups.items():
        first = observations[0]
        event, payload = first.event, first.event['payload']
        components = usage_values([record.event['payload'].get('usage') for record in observations])
        if key.startswith('unknown:'):
            components = {name: {'value': None, 'state': 'unknown',
                                 'reason': 'Response identity is incomplete'} for name in COMPONENTS}
        status = 'conflict' if any(row['state'] == 'conflict' for row in components.values()) else components['total_tokens']['state']
        result.append({'key': key, 'thread_id': payload.get('thread_id'), 'response_id': payload.get('response_id'),
                       'turn_id': payload.get('turn_id'), 'model': payload.get('model'),
                       'timestamp': event.get('timestamp'), 'kind': 'modern', 'usage_status': status,
                       'components': components, 'observations': [
                           {'byte_offset': record.offset, 'event_hash': record.event_hash,
                            'timestamp': record.event.get('timestamp')}
                           for record in observations]})
    return result


def legacy_invocations(records, session):
    previous, modern_seen, result = None, False, []
    for record in records:
        event, payload = record.event, record.event['payload']
        if event.get('type') == 'token_usage_record':
            modern_seen = True
        if modern_seen or event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            continue
        info = payload.get('info')
        info = info if isinstance(info, dict) else {}
        cumulative, last_usage = info.get('total_token_usage'), info.get('last_token_usage')
        total = cumulative.get('total_tokens') if isinstance(cumulative, dict) else None
        last = last_usage.get('total_tokens') if isinstance(last_usage, dict) else None
        if known_counter(total) and total == 0:
            previous = 0
            continue
        if known_counter(total) and previous is not None and total == previous:
            continue
        delta = total - previous if known_counter(total) and previous is not None and total >= previous else None
        known = delta is not None
        components = {name: {'value': None, 'state': 'unknown',
                             'reason': 'Legacy interval does not observe this component'} for name in COMPONENTS}
        components['total_tokens'] = {'value': delta, 'state': 'known' if known else 'unknown',
                                     'reason': None if known else 'Unestablished or reset cumulative base'}
        result.append({'key': 'legacy:' + identity([session, event.get('timestamp'), total]),
                       'thread_id': None, 'response_id': None, 'turn_id': payload.get('turn_id'),
                       'model': None, 'timestamp': event.get('timestamp'),
                       'kind': 'legacy_call' if known and delta == last else 'legacy_interval',
                       'usage_status': 'known' if known else 'unknown', 'components': components,
                       'observations': [{'byte_offset': record.offset, 'event_hash': record.event_hash,
                                         'timestamp': event.get('timestamp')}]})
        previous = total if known else None
    return result


def invocation_facts(records, session):
    return modern_invocations(records, session) + legacy_invocations(records, session)
