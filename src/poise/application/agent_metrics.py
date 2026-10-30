"""Coordinate accounting queries and the existing optional delivery port."""
from ..modules.accounting.metrics.queries import MetricQueries, committed_coverage
from ..modules.accounting.ports import MetricDeliveryPort, MetricObservationRepository


class AgentMetrics:
    def __init__(self, repository: MetricObservationRepository, delivery: MetricDeliveryPort):
        self.repository, self.delivery = repository, delivery

    def execute(self, request):
        if request.operation == 'query':
            snapshot = self.repository.metric_snapshot()
            return MetricQueries(snapshot['observations'], snapshot['usage']).query(request)
        replay = self.delivery.replay() if request.operation == 'replay' else None
        delivery = self.delivery.status()
        failed = bool(delivery['corrupt'] or delivery['exhausted'] or delivery.get('error') or delivery['failed_attempts'])
        coverage = {'state': 'complete', 'errors': []}
        if self.repository.metric_available():
            observations = self.repository.metric_snapshot()['observations']
            coverage = committed_coverage(observations)
        if failed or delivery['pending']:
            coverage['state'] = 'partial'
        result = {'schema': 'poise-agent-metrics-result-1', 'scope': request.scope,
                  'status': 'unavailable' if delivery['status'] == 'unavailable' else 'partial' if failed else 'observed',
                  'delivery': delivery, 'coverage': coverage, 'directory': str(self.delivery.directory)}
        if failed or delivery['pending'] or coverage['state'] == 'partial':
            result['continuation'] = ('Restore the observed source/destination and run telemetry replay for '
                                      + str(self.delivery.directory) + '; capture a new native event for an unavailable source.')
        if replay is not None:
            result['replay'] = replay
            result.update(replay)
            if replay['failed'] or replay['corrupt']:
                result['status'] = 'partial'
            elif delivery['status'] != 'unavailable':
                result['status'] = 'completed'
        return result

