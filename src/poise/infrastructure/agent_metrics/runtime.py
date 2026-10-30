"""Compose metric captures with the existing detached dispatcher and spool."""
from dataclasses import asdict
from threading import Lock

from ...application.agent_metrics import AgentMetrics
from ...application.telemetry import TelemetryEnvelope
from ...modules.accounting.metrics.domain import OBSERVATION_SCHEMA, identity, instant, observation_document
from ...modules.accounting.metrics.adoption import adoption_observations
from ...modules.accounting.metrics.delivery import capture_health
from ...modules.accounting.metrics.normalization import response_observations
from ...modules.accounting.metrics.invocations import invocation_facts
from ...modules.accounting.metrics.tools import tool_observations
from ...modules.accounting.metrics.task_context import task_observations
from ..clock import SystemClock
from ..sqlite.accounting import SqliteAccounting
from ..sqlite.accounting_database import AccountingSnapshotUnavailable
from ..telemetry import AsyncTelemetryDispatcher, DetachedTelemetryProcessor, TelemetryDatabase
from ..telemetry_spool import TelemetrySpool
from .sources import owned_records, prefix_digest, scan_jsonl
from .configuration import AgentMetricsPolicy
from .skill_sources import skill_source_observations


def envelope(document, session, clock):
    data = {'operation': 'agent-metrics.' + document['kind'], 'session': session, 'started': dict(clock), 'finished': dict(clock),
            'agent_metrics': {'schema': OBSERVATION_SCHEMA, **document}}
    return TelemetryEnvelope(identity(data), data)


class MetricCaptureProcessor:
    """Prepare a whole source outside the one existing accounting transaction."""
    def __init__(self, repository, clock):
        self.repository, self.clock = repository, clock
        # Spool replay retains its existing parallel source preparation. This
        # one receiver serializes its own SQLite boundaries, so its short
        # writer admission is spent on other processes, not its own threads.
        self.commit_lock = Lock()

    def commit(self, job, prepared):
        with self.commit_lock:
            self.repository.accept_metric_capture(job, prepared)

    def __call__(self, job):
        # Missing storage is ordinary on the first capture; no source I/O on a
        # replay whose committed immutable job already proves the entire batch.
        with self.commit_lock:
            try:
                if self.repository.database.path.exists() and self.repository.metric_capture_committed(job):
                    return
            except AccountingSnapshotUnavailable:
                pass  # No committed input exists; the writer rechecks atomically.
        self.commit(job, self.prepare(job))

    def prepare(self, job):
        document = observation_document(job.data['agent_metrics'])
        if document['kind'] == 'normalized-capture':
            observations = [document['observation'], *document.get('coverage_observations', [])]
            return [envelope({'kind': 'facts', **observed}, job.data['session'],
                             asdict(self.clock.observe())) for observed in observations]
        descriptor = document['descriptor']
        policy = AgentMetricsPolicy.parse(document['policy'])
        if descriptor['source']['kind'] == 'skill-policy':
            prepared = skill_source_observations(descriptor, document['scope'], policy.collection['max_source_bytes'],
                                                  job.data['started']['audit_utc'])
            return [envelope(prepared, job.data['session'], asdict(self.clock.observe()))]
        batch = scan_jsonl(descriptor, max_source_bytes=policy.collection['max_source_bytes'],
                           max_records=policy.collection['max_records'])
        if descriptor['source']['kind'] == 'hook-adoption':
            facts = adoption_observations(batch.records, self.repository.project, descriptor['source'])
            coverage = {**batch.coverage, 'source_key': batch.source_key, 'source_content_hash': batch.sha256,
                        'source_path': descriptor['path'],
                        'excluded_prefix_events': 0}
            scope = {'project': self.repository.project}
            prepared = envelope({'kind': 'facts', **facts, 'coverage': coverage, 'scope': scope,
                                 'source': descriptor['source']}, job.data['session'], asdict(self.clock.observe()))
            return [prepared]
        session, meta, records, excluded = owned_records(batch)
        clock = asdict(self.clock.observe())
        scope = {**document['scope'], 'session': session}
        association = document['task_association']
        if session != document['scope'].get('session'):
            scope.pop('task', None)
            scope.pop('sprint', None)
            association = {'state': 'not_applicable', 'reason': 'Source session differs from the observed native Task owner'}
        coverage = {**batch.coverage, 'source_key': batch.source_key, 'source_content_hash': batch.sha256,
                    'source_path': descriptor['path'], 'checkout': meta.get('cwd', ''),
                    'policy_digest': document['policy_digest'],
                    **({'native': document['native']} if 'native' in document else {}),
                    'excluded_prefix_events': len(excluded),
                    'undated_invocations': sum(not row['timestamp'] for row in invocation_facts(records, session)),
                    'unprocessed_sessions': [], 'processing_failures': [],
                    'usage_records_missing': not any(record.event.get('type') == 'token_usage_record' or
                        record.event.get('payload', {}).get('type') == 'token_count' for record in records),
                    'complete_for_registered_scope': not batch.errors and not batch.coverage['partial_tail']
                        and not batch.coverage['source_changed_since_capture']}
        stamps = [instant(record.event['timestamp']) for record in records if record.event.get('timestamp')]
        coverage.update(session_id=session, first_event_at=min(stamps).isoformat() if stamps else None,
                        last_event_at=max(stamps).isoformat() if stamps else None)
        if self.repository.database.path.exists():
            with self.commit_lock:
                try:
                    prior = self.repository.metric_snapshot()['observations']
                except AccountingSnapshotUnavailable:
                    prior = []
            previous = next((row['envelope']['agent_metrics']['coverage'] for row in reversed(prior)
                             if row['envelope']['agent_metrics'].get('kind') == 'facts' and
                             row['envelope']['agent_metrics']['coverage']['source_key'] == batch.source_key), None)
            if previous is not None and 'source_content_hash' in previous:
                changed = previous['boundary'] > descriptor['boundary'] or prefix_digest(
                    descriptor['path'], previous['boundary']) != previous['source_content_hash']
                coverage['source_changed'] = changed
                if changed:
                    coverage['complete_for_registered_scope'] = False
        facts = response_observations(records, scope, descriptor['source'], coverage, clock['audit_utc'])
        tools = tool_observations(records, scope, descriptor['source'], meta.get('cwd', ''),
                                  policy.accounting.data['path_categories'])
        facts['facts'].extend(tools['facts'])
        facts['metadata'].extend(tools['metadata'])
        health = capture_health(records, scope, descriptor['source'], descriptor, job, clock)
        facts['facts'].extend(health['facts'])
        facts['metadata'].extend(health['metadata'])
        task_facts = task_observations(association, scope, descriptor['source'], job.data['started']['audit_utc'])
        facts['facts'].extend(task_facts['facts'])
        facts['metadata'].extend(task_facts['metadata'])
        prepared = envelope({'kind': 'facts', **facts, 'coverage': coverage, 'scope': scope,
                             'source': descriptor['source']}, session, clock)
        return [prepared]


def metric_tools(project, policy, checkout):
    database = TelemetryDatabase(policy.database, policy.lock,
                                 policy.delivery.lock_seconds, policy.delivery.lock_poll_seconds)
    repository = SqliteAccounting(database, project, policy.accounting)
    clock = SystemClock()
    processor = MetricCaptureProcessor(repository, clock)
    delivery = TelemetrySpool(policy.directory, asdict(policy.delivery), processor)
    return AgentMetrics(repository, delivery)


def metric_dispatcher(service, policy):
    def deliver_capture(batch):
        # One hook capture must not race its own enqueue operations against the
        # configured short queue lock. Each physical source retains its own
        # unchanged immutable queue identity and atomic accounting transaction.
        failures = []
        for item in batch.data['agent_metrics']['jobs']:
            try:
                service.delivery.enqueue(TelemetryEnvelope(item['identity'], item['data']))
            except Exception as exc:
                failures.append(type(exc).__name__)
        service.delivery.replay()
        if failures:
            from ...modules.foundation.errors import PoiseError
            raise PoiseError('Optional capture delivery failed: ' + ', '.join(failures))
    return AsyncTelemetryDispatcher(DetachedTelemetryProcessor(deliver_capture), policy.collection['max_pending'])
