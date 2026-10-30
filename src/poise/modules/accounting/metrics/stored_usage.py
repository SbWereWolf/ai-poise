"""Expose already recorded accounting values without analytical aggregation."""
import json

from .domain import OBSERVATION_SCHEMA, ObservationFacts, identity, known_counter
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
