"""Read-only fact selection and reconciliation over immutable observations."""
from copy import deepcopy

from .domain import canonical, instant, observation_document
from .stored_usage import stored_usage_observations


def current_coverages(items):
    """Current selection generations affect coverage, not retained measurements."""
    items = list(items)
    selections = {}
    for index, item in enumerate(items):
        selected = item.get('selection')
        if selected is None:
            continue
        score = (instant(selected['observed_at']), index)
        previous = selections.get(selected['key'])
        if previous is None or score > previous[0]:
            selections[selected['key']] = (score, item)
    managed = {item['source_id'] for _, item in selections.values()}
    files = {}
    for item in items:
        if 'selection' in item:
            continue
        key = item.get('selection_key')
        if key is not None:
            selected = selections.get(key)
            if selected is not None and item.get('selection_generation') != selected[1]['selection']['generation']:
                continue
        elif item.get('source_id') in managed:
            continue
        files[item['source_key']] = item
    result = list(files.values())
    for _, item in selections.values():
        selected = item['selection']
        observed = sum(row.get('selection_key') == selected['key'] and
                       row.get('selection_generation') == selected['generation'] for row in files.values())
        value = deepcopy(item)
        if observed < selected['expected_files']:
            value['complete_for_registered_scope'] = False
            value['errors'] = [*value['errors'], {'kind': 'selection_processing_pending',
                'source': selected['address'], 'reason': 'The selected source capture has not committed completely; '
                'observe delivery status and replay pending captures.'}]
        result.append(value)
    return sorted(result, key=lambda item: item['source_key'])


def summarize_coverage(items):
    items = current_coverages(items)
    result = {'state': 'complete', 'partial_tail': any(item['partial_tail'] for item in items),
              'excluded_prefix_events': sum(item['excluded_prefix_events'] for item in items),
              'errors': [error for item in items for error in item['errors']], 'sources': items,
              'source_changed': any(item.get('source_changed', False) or item.get('source_changed_since_capture', False) for item in items),
              'complete_for_registered_scope': all(item.get('complete_for_registered_scope', True) for item in items)}
    if result['partial_tail'] or result['errors'] or result['source_changed']:
        result['state'] = 'partial'
    return result


def committed_coverage(observations):
    coverages = []
    for observed in observations:
        document = observation_document(observed['envelope']['agent_metrics'])
        if document.get('kind') == 'facts':
            coverages.append({**document['coverage'], 'source_id': document['source']['id']})
    return summarize_coverage(coverages)


def matches(item, request):
    scope = request.scope
    own = item['scope']
    if scope['kind'] == 'source':
        if item['source']['id'] != scope['id']:
            return False
    elif own.get(scope['kind']) != scope['id']:
        return False
    lower, upper = request.period['from'], request.period['to']
    if lower is not None or upper is not None:
        if not item.get('at'):
            return False
        stamp = instant(item['at'])
        if lower is not None and stamp < instant(lower) or upper is not None and stamp >= instant(upper):
            return False
    return request.view['family'] is None or item['family'] == request.view['family']


def reconcile(rows):
    """Preserve first provenance; refine unknown facts and expose actual conflicts."""
    result = deepcopy(rows[0])
    known = {}
    for row in rows:
        if row['availability']['state'] == 'known':
            known.setdefault(canonical(row['value']), row)
    conflict = any(row['availability']['state'] == 'conflict' for row in rows)
    if conflict or len(known) > 1:
        result.update(value=None, availability={'state': 'conflict',
                      'reason': 'Immutable observations disagree for the same fact'})
        supporting = list(known.values())
        explicit = next((row for row in rows if row['availability']['state'] == 'conflict'), None)
        if explicit is not None:
            supporting.append(explicit)
    elif known:
        selected = next(iter(known.values()))
        result.update(value=selected['value'], availability=deepcopy(selected['availability']))
        supporting = [selected]
    else:
        supporting = [rows[0]]
    result['_support'] = [row['_witness'] for row in supporting]
    return result


class MetricQueries:
    """No source reads, ingestion, clock or authoritative Task access."""

    def __init__(self, observations, usage):
        self.observations = observations + stored_usage_observations(usage)

    def query(self, request):
        groups, metadata, provenance = {}, {}, {}
        coverages, selections = [], []
        def contribute(witness, source, entity):
            identifier = witness['observation_id']
            entry = provenance.setdefault(identifier, {**witness, 'source': deepcopy(source), 'entities': []})
            if entity not in entry['entities']:
                entry['entities'].append(deepcopy(entity))
        for observed in self.observations:
            document = observation_document(observed['envelope']['agent_metrics'])
            if document.get('kind') != 'facts':
                continue
            witness = {'observation_id': observed['id'], 'sha256': document['coverage'].get('source_content_hash'),
                       'source_key': document['coverage']['source_key']}
            own = document['scope']
            coverage = {**document['coverage'], 'source_id': document['source']['id']}
            if 'selection' in coverage or 'selection_key' in coverage:
                selections.append(coverage)
            scope = request.scope
            if (scope['kind'] == 'source' and document['source']['id'] == scope['id'] or
                    scope['kind'] != 'source' and own.get(scope['kind']) == scope['id']):
                coverages.append(coverage)
            for item in document['facts']:
                item = {**item, '_witness': witness}
                if not matches(item, request):
                    continue
                groups.setdefault(item['key'], []).append(item)
            for item in document['metadata']:
                if matches(item, request):
                    if item['key'] not in metadata:
                        metadata[item['key']] = deepcopy(item['fields'])
                        contribute(witness, item['source'], {'metadata_key': item['key']})
        relevant = {item['selection_key'] for item in coverages if 'selection_key' in item}
        relevant.update(item['selection']['key'] for item in coverages if 'selection' in item)
        # A reached selection owns its full capture membership. File facts keep
        # their actual session scopes; coverage joins only the same selection.
        coverages.extend(item for item in selections
                         if item.get('selection_key', item.get('selection', {}).get('key')) in relevant
                         and item not in coverages)
        metrics = []
        for key in sorted(groups):
            item = reconcile(groups[key])
            for witness in item['_support']:
                contribute(witness, item['source'], {'fact_key': key, 'context': item['context']})
            metrics.append({k: item[k] for k in ('name', 'value', 'unit', 'kind', 'availability', 'context')}
                                        | {'family': item['family'], 'provenance': {'fact_key': key, 'source': item['source'], 'at': item['at'],
                                                          'observations': item['_support']}})
        coverage = summarize_coverage(coverages)
        if not coverages:
            coverage.update(state='unavailable', reason='No committed source capture covers the selected scope',
                            complete_for_registered_scope=False)
        return {'schema': 'poise-agent-metrics-result-1', 'status': 'read_only',
                'scope': deepcopy(request.scope), 'period': deepcopy(request.period),
                'metrics': metrics, 'context': list(metadata.values()),
                'provenance': list(provenance.values()), 'coverage': coverage}
