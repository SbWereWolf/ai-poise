"""Read-only fact selection and reconciliation over immutable observations."""
from copy import deepcopy
from dataclasses import replace

from .domain import canonical, instant, observation_document
from .aggregates import aggregate_readouts, stored_usage_observations
from .history import history_readouts
from .skills import content_readouts, skill_readouts


def summarize_coverage(items):
    items = list(items)
    result = {'state': 'complete', 'partial_tail': any(item['partial_tail'] for item in items),
              'excluded_prefix_events': sum(item['excluded_prefix_events'] for item in items),
              'errors': [error for item in items for error in item['errors']], 'sources': items,
              'source_changed': any(item.get('source_changed', False) or item.get('source_changed_since_capture', False) for item in items),
              'complete_for_registered_scope': all(item.get('complete_for_registered_scope', True) for item in items)}
    if result['partial_tail'] or result['errors'] or result['source_changed']:
        result['state'] = 'partial'
    return result


def committed_coverage(observations):
    latest = {}
    for observed in observations:
        document = observation_document(observed['envelope']['agent_metrics'])
        if document.get('kind') == 'facts':
            coverage = document['coverage']
            latest[coverage['source_key']] = coverage
    return summarize_coverage(latest.values())


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

    def __init__(self, observations, usage, accounting_policy, analyses):
        self.observations = observations + stored_usage_observations(usage)
        self.timezone = accounting_policy.data['timezone']
        self.week_start = accounting_policy.data['week_start']
        self.analyses = analyses

    def query(self, request):
        groups, metadata, provenance, coverages, all_groups, all_metadata = {}, {}, {}, {}, {}, {}
        analysis_records = []
        any_family = replace(request, view={**request.view, 'family': None})
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
            scope = request.scope
            if (scope['kind'] == 'source' and document['source']['id'] == scope['id'] or
                    scope['kind'] != 'source' and own.get(scope['kind']) == scope['id']):
                coverage = document['coverage']
                coverages[coverage['source_key']] = coverage
            for item in document['facts']:
                item = {**item, '_witness': witness}
                if matches(item, any_family):
                    all_groups.setdefault(item['key'], []).append(item)
                if not matches(item, request):
                    continue
                if request.view['kind'] == 'facts' and item['kind'] != 'observed':
                    continue
                groups.setdefault(item['key'], []).append(item)
            for item in document['metadata']:
                if matches(item, any_family):
                    all_metadata.setdefault(item['key'], deepcopy(item['fields']))
                    analysis_records.append({**item, '_witness': witness})
                if matches(item, request):
                    if item['key'] not in metadata:
                        metadata[item['key']] = deepcopy(item['fields'])
                        contribute(witness, item['source'], {'metadata_key': item['key']})
        metrics = []
        for key in sorted(groups):
            item = reconcile(groups[key])
            for witness in item['_support']:
                contribute(witness, item['source'], {'fact_key': key, 'context': item['context']})
            metrics.append({k: item[k] for k in ('name', 'value', 'unit', 'kind', 'availability', 'context')}
                                        | {'family': item['family'], 'provenance': {'fact_key': key, 'source': item['source'], 'at': item['at'],
                                                          'observations': item['_support']}})
        if request.view['kind'] == 'derived' and request.view['family'] in (None, 'aggregate-readouts'):
            metrics.extend({**row, 'family': 'aggregate-readouts'} for row in aggregate_readouts(
                [reconcile(rows) for rows in all_groups.values()], list(all_metadata.values()), self.timezone))
            if metrics and not provenance:
                for key, rows in all_groups.items():
                    item = reconcile(rows)
                    for witness in item['_support']:
                        contribute(witness, item['source'], {'fact_key': key, 'context': item['context']})
            if not any(row['scope'].get('task') for rows in all_groups.values() for row in rows):
                metadata['task-coverage'] = {'field_availability': {'complete_for_bound_sessions': {
                    'source_id': 'task-adapter', 'state': 'not_applicable',
                    'reason': 'These observations have no Task association'}}}
        if request.view['kind'] == 'derived' and request.view['family'] in (None, 'history') and 'history' in self.analyses:
            history, details = history_readouts([reconcile(rows) for rows in all_groups.values()],
                                               coverages.values(), self.analyses['history'], self.week_start)
            metrics.extend({**row, 'family': 'history'} for row in history)
            metadata['history-sources'] = details
            if history:
                for key, rows in all_groups.items():
                    item = reconcile(rows)
                    for witness in item['_support']:
                        contribute(witness, item['source'], {'fact_key': key, 'context': item['context']})
        skill_families = ('skill-selection', 'skill-references', 'skill-content')
        if request.view['kind'] == 'derived' and request.view['family'] in (None, *skill_families):
            views, observed_skills, observed_references, inventory = skill_readouts(
                [reconcile(rows) for rows in all_groups.values()], analysis_records, self.analyses['skill_content'])
            views['skill-content'] = content_readouts(inventory, observed_skills, observed_references,
                                                      self.analyses['skill_content'])
            for family, rows in views.items():
                if request.view['family'] in (None, family):
                    metrics.extend({**row, 'family': family} for row in rows)
            for key, rows in all_groups.items():
                item = reconcile(rows)
                for witness in item['_support']:
                    contribute(witness, item['source'], {'fact_key': key, 'context': item['context']})
            for item in analysis_records:
                if item['fields'].get('record_type') in ('effective-skill-policy', 'skill-routing', 'skill-inventory'):
                    contribute(item['_witness'], item['source'], {'metadata_key': item['key']})
        coverage = summarize_coverage(coverages.values())
        if not coverages:
            coverage.update(state='unavailable', reason='No committed source capture covers the selected scope',
                            complete_for_registered_scope=False)
        return {'schema': 'poise-agent-metrics-result-1', 'status': 'read_only',
                'scope': deepcopy(request.scope), 'period': deepcopy(request.period),
                'metrics': metrics, 'context': list(metadata.values()),
                'provenance': list(provenance.values()), 'coverage': coverage}
