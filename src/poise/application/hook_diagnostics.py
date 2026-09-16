"""Explicit hook diagnostics through existing feature owners, not an execution gate."""
from copy import deepcopy

from ..modules.foundation.errors import PoiseError


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or '\0' in value or len(value) > 4096:
        raise PoiseError(f'{name}: bounded nonempty text required')


def _request(value):
    fields = {'definition_path', 'session_id', 'event', 'event_id', 'run_ids', 'probe_cwd'}
    if not isinstance(value, dict) or set(value) != fields:
        raise PoiseError('diagnose: explicit definition_path/session_id/event/event_id/run_ids/probe_cwd required')
    for name in ('definition_path', 'session_id', 'event'):
        _text(value[name], name)
    if value['session_id'] in ('.', '..') or '/' in value['session_id'] or '\\' in value['session_id']:
        raise PoiseError('session_id must be an exact bound session identifier')
    if value['event_id'] is not None and (type(value['event_id']) is not int or value['event_id'] < 1):
        raise PoiseError('event_id must be a positive integer or null')
    ids = value['run_ids']
    if (not isinstance(ids, list) or len(ids) > 32
            or any(not isinstance(i, str) or not i or len(i) > 256 for i in ids)
            or len(set(ids)) != len(ids)):
        raise PoiseError('run_ids must be a bounded unique list')
    if value['event'] != 'SessionEnd' and ids:
        raise PoiseError('run_ids apply only to SessionEnd')
    if value['probe_cwd'] is not None:
        _text(value['probe_cwd'], 'probe_cwd')
    return deepcopy(value)


class HookDiagnostics:
    """The port observes existing stores and runs probes only when explicitly asked."""
    def __init__(self, port):
        self.port = port

    def invoke(self, raw):
        batch = isinstance(raw, dict) and set(raw) == {'queries'}
        if batch:
            queries = raw['queries']
            if not isinstance(queries, list) or not 1 <= len(queries) <= 32:
                raise PoiseError('queries: expected 1..32 diagnostic queries')
            parsed, seen = [], set()
            for q in queries:
                if not isinstance(q, dict) or set(q) != {'id', 'request'}:
                    raise PoiseError('Each query must contain id and request')
                _text(q['id'], 'query id')
                if q['id'] in seen:
                    raise PoiseError('Duplicate diagnostic query id')
                seen.add(q['id']); parsed.append((q['id'], _request(q['request'])))
        else:
            parsed = [(None, _request(raw))]
        # All shape and explicit cwd checks precede all active operations in a batch.
        for _, req in parsed:
            self.port.validate_paths(req)
        results = [{'id': qid, 'result': self._one(req)} for qid, req in parsed]
        if not batch:
            return results[0]['result']
        return {'status': 'diagnosed' if all(r['result']['status'] == 'diagnosed' for r in results)
                else 'inconclusive', 'results': results}

    def _one(self, req):
        result = self.port.snapshot(req)
        event = result['native_event']
        effect = result['effect']
        if effect['status'] == 'observed' and event['status'] == 'observed':
            if req['event'] == 'SessionStart':
                context = effect['context']
                matched = (context['event_id'] == f"hook-event:{event['id']}"
                           and context['reason'] == event.get('source')
                           and event.get('source') in ('startup', 'resume', 'compact', 'clear'))
                effect['status'] = 'confirmed' if matched else 'not_correlated'
            elif req['event'] == 'UserPromptSubmit':
                effect['status'] = 'confirmed'
            elif req['event'] == 'SessionEnd':
                for run in effect['runs']:
                    if run['status'] != 'observed':
                        continue
                    correlation = run.get('cancellation_observation')
                    matched = (run.get('cancelled') is True
                               and type(run.get('actual_exit_code')) is int
                               and run['actual_exit_code'] < 0
                               and run.get('timed_out') is False
                               and run.get('cancellation_reason') == 'native_session_end'
                               and isinstance(correlation, dict)
                               and correlation.get('session_id') == req['session_id']
                               and type(correlation.get('end_event_id')) is int
                               and correlation.get('end_event_id') == event['id']
                               and run.get('start_observed') is True)
                    run['status'] = 'confirmed' if matched else 'not_correlated'
                effect['status'] = ('confirmed' if effect['runs'] and
                    all(r['status'] == 'confirmed' for r in effect['runs']) else 'not_correlated')
        checks = None
        if req['probe_cwd'] is not None:
            checks = self.port.probes(req)
        result['capability_checks'] = checks
        ready = (result['installation']['status'] == 'configured'
                 and result['binding']['status'] == 'valid'
                 and event['status'] == 'observed'
                 and effect['status'] in ('confirmed', 'not_configured')
                 and (checks is None or checks.get('ready') is True))
        result['status'] = 'diagnosed' if ready else 'inconclusive'
        return result
