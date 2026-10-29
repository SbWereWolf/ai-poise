"""Explicit byte-based context proxies bounded by actual model and compaction anchors."""
import math

from .domain import ObservationFacts, identity, instant
from .invocations import invocation_facts
from .tools import classification, text


def exposure_observations(records, scope, source, method, source_key, excluded_inherited_rows):
    calls = {row['observations'][0]['byte_offset'] for row in invocation_facts(records, scope['session'])}
    inputs, generation, outputs, boundaries = {}, 0, [], []
    for record in records:
        event, payload = record.event, record.event['payload']
        kind, call = payload.get('type'), payload.get('call_id')
        if event.get('type') == 'compacted' or kind == 'context_compacted':
            boundaries.append((record.offset, event.get('timestamp'), 'compaction'))
            generation += 1
        if kind in ('task_complete', 'task_completed'):
            boundaries.append((record.offset, event.get('timestamp'), 'turn-complete'))
        if event.get('type') == 'response_item' and kind in ('custom_tool_call', 'function_call'):
            body = text(payload.get('input') if kind == 'custom_tool_call' else payload.get('arguments'))
            inputs[call] = (payload.get('name'), classification(payload.get('name', ''), body))
        if event.get('type') == 'response_item' and kind in ('custom_tool_call_output', 'function_call_output'):
            outputs.append((record, generation, len(text(payload.get('output')).encode())))
    result = {'facts': [], 'metadata': []}
    for record, generation, size in outputs:
        call = record.event['payload'].get('call_id')
        bound = next((row for row in boundaries if row[0] > record.offset), None)
        end = None if bound is None else bound[0]
        following = sorted(offset for offset in calls if offset > record.offset and (end is None or offset < end))
        category = inputs.get(call, (None, 'other'))
        context = {'source_kind': 'tool', 'source_id': call, 'source_name': category[0],
                   'category': category[1], 'context_generation': generation,
                   'boundary_kind': None if bound is None else bound[2],
                   'survived_compaction': False, 'inherited_copy': False, 'estimation_basis': method,
                   'source_offset': record.offset,
                   'first_seen_model_call': following[0] if following else None,
                   'last_possible_model_call': following[-1] if following else None}
        at = record.event.get('timestamp')
        duration = None
        if at and bound is not None and bound[1]:
            measured = (instant(bound[1]) - instant(at)).total_seconds()
            duration = measured if measured >= 0 else None
        span = len(following)
        tokens = math.ceil(size / 4)
        facts = ObservationFacts(scope, source, at)
        entity = identity([scope['session'], call, record.offset])
        rows = (
            ('raw_output_bytes', size, 'UTF-8 bytes', 'observed'),
            ('raw_output_tokens', tokens, 'estimated tokens (ceil(bytes/4))', 'proxy'),
            ('exposure_span_calls', span, 'count', 'derived'),
            ('gross_context_exposure', tokens * span, 'estimated tokens multiplied by subsequent calls', 'proxy'),
            ('observations', 1, 'count', 'derived'),
            ('compaction_bounded', int(bound is not None and bound[2] == 'compaction'), 'count', 'derived'),
            ('exposure_span_seconds', duration, 'seconds', 'derived'),
            ('avg_exposure_span_seconds', duration, 'seconds', 'derived'),
            ('avg_exposure_span_calls', span, 'calls', 'derived'),
        )
        for name, value, unit, kind in rows:
            facts.add('exposure', entity, name, value, unit, kind=kind,
                      state='known' if value is not None else 'unknown', context=context,
                      reason=None if value is not None else 'No comparable source lifetime boundary')
        facts.describe('exposure', entity, {**context, 'field_availability': {
            'path': {'source_id': source['id'], 'state': 'not_applicable',
                     'reason': 'Tool output has no observed file attribution'}}})
        result['facts'].extend(facts.facts)
        result['metadata'].extend(facts.metadata)
    summary = ObservationFacts(scope, source, records[0].event.get('timestamp') if records else None)
    summary.add('exposure', identity([scope['session'], source_key, 'inherited-exclusion']),
                'excluded_inherited_rows', excluded_inherited_rows, 'count', kind='derived', state='known',
                context={'scope': 'summary', 'source_key': source_key})
    result['facts'].extend(summary.facts)
    return result
