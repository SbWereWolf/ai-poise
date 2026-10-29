"""Alternative summaries derived from reconciled facts, without extra charges."""
import json
from zoneinfo import ZoneInfo

from .domain import OBSERVATION_SCHEMA, ObservationFacts, identity, instant, known_counter
from .invocations import COMPONENTS


def stored_usage_observations(rows):
    """Project retained accounting usage in memory; never migrate stored rows."""
    result = []
    for row in rows:
        stored = json.loads(row['data'])
        sample, contribution = stored['sample'], stored['contribution']
        scope = {'project': row['project'], 'session': stored['session'],
                 'task': stored['binding']['task'], 'sprint': stored['binding']['sprint']}
        source = {'id': row['source'], 'kind': 'stored-accounting-usage', 'parser_version': '1'}
        at = sample['occurred_at']
        facts = ObservationFacts(scope, source, at)
        context = {'session_id': stored['session'], 'usage_id': row['id'], 'stream': row['stream'],
                   'event_id': sample['event_id'], 'source_mode': stored['source_mode']}
        for name in COMPONENTS:
            value = contribution.get('reasoning_tokens' if name == 'reasoning_output_tokens' else name)
            valid = known_counter(value)
            facts.add('usage-current', row['id'], name, value if valid else None, 'tokens',
                      kind='observed', state='known' if valid else 'unknown', context=context,
                      reason=None if valid else 'Retained usage does not observe this component')
        result.append({'id': row['id'], 'envelope': {'agent_metrics': {
            'schema': OBSERVATION_SCHEMA, 'kind': 'facts', **facts.export(), 'scope': scope, 'source': source,
            'coverage': {'source_key': identity([row['source'], row['stream']]), 'partial_tail': False,
                         'excluded_prefix_events': 0, 'errors': []}}}})
    return result


def aggregate_readouts(facts, metadata, timezone_name):
    usage = [row for row in facts if row['family'] == 'usage-current' and row['name'] == 'total_tokens']
    if not usage:
        return []
    known = [row for row in usage if row['availability']['state'] == 'known']
    unknown = len(usage) - len(known)
    tokens = sum(row['value'] for row in known)
    sessions = {row['scope'].get('session') for row in usage if row['scope'].get('session') is not None}
    turns = {(row['scope'].get('session'), row['context'].get('turn_id')) for row in usage
             if row['context'].get('turn_id') is not None}
    days = {instant(row['at']).astimezone(ZoneInfo(timezone_name)).date() for row in usage if row['at']}
    timings = [row for row in facts if row['family'] == 'turn-timing-context' and row['name'] == 'duration_ms']
    wall = sum(row['value'] for row in timings) / 1000 if timings and all(
        row['availability']['state'] == 'known' for row in timings) else None
    classes = {row['invocation_key']: row.get('classification', 'unclassified') for row in metadata
               if 'invocation_key' in row}
    partitions = dict.fromkeys(('main', 'auxiliary', 'unclassified'), 0)
    for row in known:
        category = classes.get(row['context'].get('invocation_key'), 'unclassified')
        partitions[category if category in partitions else 'unclassified'] += row['value']
    values = {'invocations': len(usage), 'unknown_usage': unknown, 'known_tokens': tokens,
              'total_tokens': None if unknown else tokens, 'sessions': len(sessions),
              'turns': len(turns), 'days': len(days), 'wall_seconds': wall,
              **{name + '_tokens': value for name, value in partitions.items()}}
    result = []
    for name, value in values.items():
        unit = 'tokens' if name.endswith('_tokens') else 'seconds' if name == 'wall_seconds' else 'count'
        result.append({'name': name, 'value': value, 'unit': unit, 'kind': 'derived',
                       'availability': {'state': 'known' if value is not None else 'unknown',
                           'reason': None if value is not None else 'Required source observations are incomplete'},
                       'context': {'scope': 'summary'}})
    return result
