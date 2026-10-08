"""Consistent accepted-history reads behind the existing Task repository."""
from copy import deepcopy
import hashlib
import json

from ...modules.verification.domain import method_expectation_digest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def accepted_sources(db, task_id):
    events = [(row['seq'], json.loads(row['data'])) for row in db.execute(
        'SELECT seq,data FROM task_events WHERE task_id=? ORDER BY seq', (task_id,))]
    boundaries = [seq for seq, event in events if event['event'] == 'restarted_newborn']
    if not boundaries:
        return None
    boundary = boundaries[-1]
    events = [event for seq, event in events if seq < boundary]
    workflow = json.loads(db.execute('SELECT data FROM task_workflows WHERE task_id=?', (task_id,)).fetchone()[0])
    definitions = [json.loads(row[0])['method'] for row in db.execute(
        'SELECT data FROM task_methods WHERE task_id=? ORDER BY method_id', (task_id,))]
    for history in workflow['registry']['history']:
        definitions.extend(entry['method'] for entry in history['entries'])
    submitted = {event['submission'] for event in events if event['event'] == 'submitted'}
    sources = {}
    for row in db.execute(
        'SELECT s.seq,s.stage,s.iteration,s.digest,s.data,r.data AS report,w.data AS workflow '
        'FROM submissions s LEFT JOIN task_results r ON r.task_id=s.task_id AND r.submission_id=s.seq '
        'LEFT JOIN workflow_layers w ON w.task_id=s.task_id AND w.submission_id=s.seq '
        'WHERE s.task_id=? ORDER BY s.seq', (task_id,)):
        if row['seq'] not in submitted:
            continue
        verified = [event for event in events if event['event'] == 'verified'
                    and event.get('submission') == row['seq']]
        # Unverified authored attempts are not accepted sources, but a missing
        # result/event of an otherwise verified pair is corrupted history.
        if row['report'] is None and not verified:
            continue
        source = {'stage': row['stage'], 'visit_id': row['seq'], 'digest': row['digest'],
                  'iteration': row['iteration'], 'reason': None}
        sources[row['stage']] = source
        if row['report'] is None or row['workflow'] is None or len(verified) != 1:
            source['reason'] = 'history_unavailable'
            continue
        report, flow = json.loads(row['report']), json.loads(row['workflow'])
        if (verified[0]['stage'] != row['stage'] or verified[0]['iteration'] != row['iteration']
                or report['stage'] != row['stage'] or report['iteration'] != row['iteration']
                or report['status'] != 'verified' or report['verification_commit'] != report['commit']
                or any(check['commit'] != report['commit'] or check['tree'] != report['verified_tree']
                       for check in report['checks'])):
            source['reason'] = 'history_unavailable'
            continue
        source.update(commit=report['commit'], tree=report['verified_tree'], report=report,
                      workflow=flow, envelope=json.loads(row['data']), methods=[])
        for receipt in report['checks']:
            if receipt['method'] not in {method['id'] for method in definitions}:
                source['methods_missing'] = True
            for method_id in receipt['obligations']:
                matches = {}
                for method in definitions:
                    if method['id'] != method_id:
                        continue
                    expectation = method_expectation_digest(method)
                    if expectation == receipt['expectation_digest'] and method['argv'] == receipt['argv']:
                        matches[digest(method)] = method
                if len(matches) != 1:
                    source['methods_missing'] = True
                else:
                    method = deepcopy(next(iter(matches.values())))
                    if method not in source['methods']:
                        source['methods'].append(method)
    # An accepted review superseded by rework must not survive into the new
    # downstream lineage. A subsequent submitted visit fixes the rework target.
    for index, event in enumerate(events):
        if event['event'] == 'user_rework':
            following = next((e for e in events[index + 1:] if e['event'] == 'submitted'), None)
            if following is not None:
                stage, visited = following['stage'], set()
                while stage is not None and stage not in visited:
                    visited.add(stage)
                    old = sources.get(stage)
                    if old is None:
                        break
                    next_stage = old.get('workflow', {}).get('next_stage')
                    if old['visit_id'] < following['submission']:
                        del sources[stage]
                    stage = next_stage
    return sources
