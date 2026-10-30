"""Turn owned response observations into immutable accounting facts."""
from .domain import ObservationFacts, canonical, identity
from .invocations import PARSER_VERSION, invocation_facts
from .turns import turn_observations


def response_observations(records, scope, source, coverage, processed_at):
    session = scope['session']
    invocations = invocation_facts(records, session)
    result = ObservationFacts(scope, source, None)
    for invocation in invocations:
        key = invocation['key']
        at = invocation['timestamp']
        facts = ObservationFacts(scope, source, at)
        context = {'session_id': session, 'turn_id': invocation['turn_id'],
                   'response_id': invocation['response_id'], 'invocation_key': key}
        for name, component in invocation['components'].items():
            facts.add('usage-current', key, name, component['value'], 'tokens', kind='observed',
                      state=component['state'], reason=component['reason'], context=context)
        first = invocation['observations'][0]
        metadata = {**context, **coverage, 'thread_id': invocation['thread_id'],
                    'root_session_id': invocation.get('root_session_id'),
                    'model': invocation['model'], 'timestamp': at,
                    'kind': invocation['kind'], 'usage_status': invocation['usage_status'],
                    'parser_version': PARSER_VERSION,
                    'byte_offset': first['byte_offset'], 'event_hash': first['event_hash'],
                    'processed_at': processed_at,
                    'field_availability': {}}
        if invocation['model'] is None:
            metadata['field_availability']['model'] = {'source_id': source['id'], 'state': 'unknown',
                                                       'reason': 'Response does not report its model'}
        facts.describe('invocation-provenance', key, metadata)
        result.facts.extend(facts.facts)
        result.metadata.extend(facts.metadata)
    for extra in (turn_observations(records, scope, source),):
        result.facts.extend(extra['facts'])
        result.metadata.extend(extra['metadata'])
    return result.export()
