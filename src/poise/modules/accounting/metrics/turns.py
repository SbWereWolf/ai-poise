"""Source-observed turn timing and alternative usage projections."""
from .domain import ObservationFacts, identity, instant, known_counter
import math


def turn_observations(records, scope, source):
    turns, current, session_meta = {}, None, {}
    for record in records:
        event, payload = record.event, record.event['payload']
        if event.get('type') == 'session_meta':
            session_meta = payload
        turn = payload.get('turn_id') or current
        if payload.get('type') == 'task_started':
            current, turn = payload.get('turn_id'), payload.get('turn_id')
        if not turn:
            continue
        row = turns.setdefault(turn, {'start': None, 'end': None, 'first': None, 'model': None,
                                     'effort': None, 'capacity': None, 'compactions': 0,
                                     'duration': None, 'ttft': None, 'status': 'unknown'})
        stamp = event.get('timestamp')
        if payload.get('type') == 'task_started' and row['start'] is None:
            row['start'] = stamp
        if payload.get('type') in ('task_complete', 'task_completed', 'turn_aborted'):
            row['end'] = stamp
            row['status'] = 'aborted' if payload['type'] == 'turn_aborted' else 'completed'
            for field, name in (('duration_ms', 'duration'), ('time_to_first_token_ms', 'ttft')):
                value = payload.get(field)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    row[name] = value
        if row['first'] is None and (payload.get('type') == 'agent_message' or
                event.get('type') == 'response_item' and payload.get('type') == 'message'):
            row['first'] = stamp
        if event.get('type') == 'compacted' or payload.get('type') == 'context_compacted':
            row['compactions'] += 1
        settings = payload.get('thread_settings') if payload.get('type') == 'thread_settings_applied' else payload
        if isinstance(settings, dict):
            if isinstance(settings.get('model'), str):
                row['model'] = settings['model']
            effort = settings.get('reasoning_effort') or settings.get('effort')
            if isinstance(effort, str):
                row['effort'] = effort
        info = payload.get('info')
        if known_counter(payload.get('model_context_window')):
            row['capacity'] = payload['model_context_window']
        if isinstance(info, dict) and known_counter(info.get('model_context_window')):
            row['capacity'] = info['model_context_window']
    result = {'facts': [], 'metadata': []}
    for turn, row in turns.items():
        context = {'session_id': scope['session'], 'turn_id': turn}
        entity = identity([scope['session'], turn])
        facts = ObservationFacts(scope, source, row['start'])
        def elapsed(end):
            if row['start'] is None or end is None:
                return None
            delta = (instant(end) - instant(row['start'])).total_seconds() * 1000
            return delta if delta >= 0 else None
        for name, value, unit in (
                ('duration_ms', row['duration'] if row['duration'] is not None else elapsed(row['end']), 'milliseconds'),
                ('time_to_first_token_ms', row['ttft'] if row['ttft'] is not None else elapsed(row['first']), 'milliseconds'),
                ('model_context_window', row['capacity'], 'tokens capacity'),
                ('compactions', row['compactions'], 'count')):
            facts.add('turn-timing-context', entity, name, value, unit, kind='observed',
                      state='known' if value is not None else 'unknown', context=context,
                      reason=None if value is not None else 'Required source timing/context observation is absent')
        fields = {**context, 'started_at': row['start'], 'completed_at': row['end'],
                  'status': row['status'],
                  'model': row['model'], 'reasoning_effort': row['effort'],
                  'cli_version': session_meta.get('cli_version')}
        facts.describe('turn-timing-context', entity, fields)
        result['facts'].extend(facts.facts)
        result['metadata'].extend(facts.metadata)
    return result


def alternative_usage(invocations, scope, source):
    groups = {}
    for invocation in invocations:
        groups.setdefault(invocation['turn_id'], []).append(invocation)
    result = {'facts': [], 'metadata': []}
    for turn, rows in groups.items():
        context = {'session_id': scope['session'], 'turn_id': turn}
        entity = identity([scope['session'], turn])
        facts = ObservationFacts(scope, source, rows[0]['timestamp'])
        values = {'model_calls': len(rows)}
        for name in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens'):
            components = [row['components'][name] for row in rows]
            values[name] = sum(row['value'] for row in components) if all(
                row['state'] == 'known' for row in components) else None
        raw, cached = values['input_tokens'], values['cached_input_tokens']
        values['uncached_input_tokens'] = raw - cached if raw is not None and cached is not None else None
        for name, value in values.items():
            facts.add('legacy-turn-usage', entity, name, value, 'count' if name == 'model_calls' else 'tokens',
                      kind='derived', state='known' if value is not None else 'unknown', context=context,
                      reason=None if value is not None else 'One or more response components are unavailable')
        result['facts'].extend(facts.facts)
    return result
