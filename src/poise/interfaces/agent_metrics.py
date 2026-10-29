"""Explicit taskless metric query transport, with complete machine output."""
import json
from pathlib import Path
import sqlite3
import uuid

from ..infrastructure.agent_metrics.configuration import AgentMetricsPolicy
from ..infrastructure.agent_metrics.runtime import metric_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout
from ..modules.accounting.metrics.domain import MetricFailure, MetricRequest
from ..modules.foundation.errors import PoiseError


def execute(config_path, checkout, policy_path, audience, stream, output):
    bound, policy, request = None, None, None
    result = {'schema': 'poise-agent-metrics-result-1', 'status': 'rejected'}
    try:
        if not all(Path(path).is_absolute() for path in (config_path, checkout, policy_path)):
            raise PoiseError('Select absolute config, checkout and policy paths')
        bound = read_project_checkout(config_path, checkout)
        policy = AgentMetricsPolicy.read(policy_path)
        limit = bound.config['batch']['max_input_bytes']
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise PoiseError('Metric request exceeds the selected input bound')
        request = MetricRequest.parse(strict_json(raw.decode('utf-8')), bound.config['project'])
        if request.operation == 'query' and not policy.database.is_file():
            raise MetricFailure('unavailable', 'No committed metric history at the selected database',
                                'Enable the agent-metrics capability and deliver an ordinary native event.')
        result = metric_tools(bound.config['project'], policy).execute(request)
    except MetricFailure as exc:
        result.update(status=exc.status, reason=exc.reason, continuation=exc.continuation)
    except (PoiseError, OSError, ValueError, UnicodeError, sqlite3.DatabaseError, RecursionError) as exc:
        result.update(status='rejected', reason=str(exc),
                      continuation='Correct the explicit request or selected metric policy; preserve existing history.')
    code = (2 if result['status'] in ('rejected', 'unavailable') else
            1 if result['status'] == 'partial' and request is not None and request.operation == 'replay' else 0)
    if audience == 'agent' and bound is not None and policy is not None:
        from ..infrastructure.result_views import SavedResponse
        saved = SavedResponse(policy.root / 'responses' / uuid.uuid4().hex / 'response.json',
                              bound.config['batch']['file_mode'])
        saved.save(result, canonical=True)
        result = saved.view(result, 'brief', bound.config['limits']['output_chars'])
    output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
    return code
