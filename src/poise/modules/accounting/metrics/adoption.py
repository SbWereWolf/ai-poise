"""Registered hook-adoption observations, with typed fields and no raw command archive."""
from .domain import ObservationFacts, identity, known_counter


FIELDS = ('session_id', 'turn_id', 'tool_use_id', 'subagent_id', 'scope_key', 'hook_event_name',
          'rule_id', 'result', 'profile', 'command_profile', 'transport', 'mcp_availability',
          'fallback_reason', 'terminal_status', 'input_sha256', 'observed_at')


def adoption_observations(records, project, source):
    result = {'facts': [], 'metadata': []}
    for record in records:
        event = record.event
        session = event.get('session_id')
        if not isinstance(session, str) or not session:
            continue
        scope = {'project': project, 'session': session}
        context = {name: event.get(name) for name in FIELDS}
        context.update(source=source['id'], source_offset=record.offset, event_sha256=record.event_hash)
        facts = ObservationFacts(scope, source, event.get('observed_at'))
        entity = identity([source['id'], session, record.event_hash])
        for name in ('rewrite', 'blocked', 'bypass_attempt'):
            value = event.get(name)
            valid = type(value) is bool
            facts.add('hook-adoption', entity, name, value if valid else None, 'boolean',
                      kind='observed', state='known' if valid else 'unknown', context=context,
                      reason=None if valid else 'Registered event does not observe a boolean outcome')
        value = event.get('input_bytes')
        valid = known_counter(value)
        facts.add('hook-adoption', entity, 'input_bytes', value if valid else None, 'bytes',
                  kind='observed', state='known' if valid else 'unknown', context=context,
                  reason=None if valid else 'Registered event does not observe input size')
        facts.add('hook-adoption', entity, 'payload_bytes', record.size, 'bytes', kind='observed',
                  state='known', context=context)
        facts.describe('hook-adoption', entity, context)
        result['facts'].extend(facts.facts)
        result['metadata'].extend(facts.metadata)
    return result
