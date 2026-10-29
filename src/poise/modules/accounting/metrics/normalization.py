"""Turn owned response observations into immutable accounting facts."""
from .domain import ObservationFacts, canonical, identity
from .invocations import CLASSIFICATION_VERSION, PARSER_VERSION, invocation_facts
from .purpose import classify_invocation_purpose
from .turns import alternative_usage, turn_observations


def response_observations(records, scope, source, coverage, processed_at):
    session = scope['session']
    invocations = invocation_facts(records, session)
    result = ObservationFacts(scope, source, None)
    tools = {}
    for record in records:
        event, payload = record.event, record.event['payload']
        if event.get('type') != 'response_item' or payload.get('type') not in ('custom_tool_call', 'function_call'):
            continue
        args = payload.get('input') if payload['type'] == 'custom_tool_call' else payload.get('arguments')
        meta = payload.get('internal_chat_message_metadata_passthrough')
        response = payload.get('response_id') or (meta.get('response_id') if isinstance(meta, dict) else None)
        tool = {'call_id': payload.get('call_id'), 'name': payload.get('name'),
                'arguments': args if isinstance(args, str) else canonical(args),
                'source_offset': record.offset, 'source_path': coverage['source_path'],
                'event_sha256': record.event_hash, 'response_id': response,
                'link_reason': 'explicit response_id' if response else 'No explicit response identity.'}
        tools.setdefault(identity([tool['call_id'], tool['name'], tool['arguments'], response]), tool)
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
        linked = [tool for tool in tools.values() if invocation['response_id'] is not None and
                  tool['response_id'] == invocation['response_id']]
        purpose = classify_invocation_purpose(linked, repository_root=coverage['checkout'])
        metadata = {**context, **coverage, 'thread_id': invocation['thread_id'],
                    'model': invocation['model'], 'timestamp': at,
                    'kind': invocation['kind'], 'usage_status': invocation['usage_status'],
                    'parser_version': PARSER_VERSION, 'classification_version': CLASSIFICATION_VERSION,
                    'byte_offset': first['byte_offset'], 'event_hash': first['event_hash'],
                    'processed_at': processed_at,
                    'classification': purpose['classification'],
                    'classification_evidence_hash': identity(linked),
                    'classification_reason': purpose['classification_reason'],
                    'field_availability': {}}
        if invocation['model'] is None:
            metadata['field_availability']['model'] = {'source_id': source['id'], 'state': 'unknown',
                                                       'reason': 'Response does not report its model'}
        facts.describe('invocation-provenance', key, metadata)
        result.facts.extend(facts.facts)
        result.metadata.extend(facts.metadata)
    for extra in (alternative_usage(invocations, scope, source), turn_observations(records, scope, source)):
        result.facts.extend(extra['facts'])
        result.metadata.extend(extra['metadata'])
    return result.export()
