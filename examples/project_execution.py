"""Continue the planned real-Poise pilot for ONE execution stage, then export.

This is an application-specific example, not another route engine. It invokes
Project/Task/Transfer through the existing public tools and never edits their
configuration or databases. A user must explicitly request ``continue``.
The execution stage is NOT accepted; its result and task travel together.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from poise.application.work import WorkTools
from poise.runtime import Poise
from poise.infrastructure.clock import SystemClock


def continue_execution(*, config_path: Path, session: str, task_id: str,
                       user_decision: str, export_request_id: str) -> dict:
    if user_decision != 'continue':
        raise ValueError('Explicit user decision continue is required for this example')
    h = Poise(config_path, session, SystemClock())
    record = h.task_queries.record(task_id)
    if record is None or record['contract']['goal_type'] != 'verification':
        raise ValueError('The example requires the previously planned verification task')
    if record['process']['stages'][record['stage_index']]['id'] != 'planning':
        raise ValueError('This example only advances planning to execution; no further stage is accepted')
    client = WorkTools(h)

    def call(operation, inputs):
        return client.invoke({'operation': operation, 'input': inputs, 'messages': []})

    # Resolve a restored task with its saved process position, never reconstruct
    # it from a status string or patch a database row.
    context = call('bootstrap', {'task': {'id': task_id}, 'decision': None,
                                 'feedback': None, 'rework_stage': None})
    if context['status'] not in ('verified', 'accepted'):
        raise ValueError('The planning stage must already have a verified result')
    context = call('bootstrap', {'task': None, 'decision': user_decision,
                                 'feedback': None, 'rework_stage': None})
    if context['stage'] != 'execution':
        raise ValueError('Selected process did not open the expected execution stage')
    tree = Path(context['worktree'])
    git_timeout = h.cfg['limits']['git_seconds']

    def git(*args):
        return subprocess.check_output(['git', '-C', str(tree), *args], text=True,
                                       timeout=git_timeout).strip()

    before = git('rev-parse', 'HEAD')
    result = deepcopy(context['result_template'])
    result['sections']['report'] = (
        'Execute the accepted exact verification methods against the pinned Poise source. '
        'The Poise supplies command observations; this submission does not claim an outcome '
        'before execution. Stop after execution: interpretation and inspection are later stages.')
    report = call('verify', {'result': result, 'artifacts': []})
    if report['status'] != 'verified':
        return {'status': report['status'], 'stage': 'execution', 'report': report,
                'execution_accepted': False, 'next_stage_started': False, 'transfer': None}
    replay = call('verify', {'result': None, 'artifacts': []})
    changed = bool(git('status', '--porcelain')) or git('rev-parse', 'HEAD') != before
    if changed or not replay['replayed']:
        raise RuntimeError('Read-only execution/replay invariant did not hold')
    exported = call('transfer', {
        'action': 'export', 'request_id': export_request_id,
        'task_ids': [task_id], 'sprint_id': None,
        'handoff': {'request_id': export_request_id + '-handoff',
                    'reason': 'Preserve verified execution for later analysis; no acceptance of execution.',
                    'result': None, 'commit_message': None, 'artifact_paths': []}})
    saved = h.task_queries.record(task_id)
    return {'status': 'verified', 'kind': 'real_source_execution_checkpoint',
            'task_id': task_id, 'stage': 'execution', 'iteration': context['iteration'],
            'target_revision': before, 'target_changed': changed,
            'execution_accepted': False, 'next_stage_started': False,
            'user_decision': user_decision, 'repeat_replayed': replay['replayed'],
            'source_owner_released': saved['claimed_by'] is None,
            'report': report, 'transfer': exported,
            'token_usage': 'unavailable', 'user_events': 'not_injected'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--user-decision', required=True, choices=('continue',))
    parser.add_argument('--export-request-id', required=True)
    args = parser.parse_args()
    outcome = continue_execution(config_path=args.config, session=args.session,
                                 task_id=args.task_id, user_decision=args.user_decision,
                                 export_request_id=args.export_request_id)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome['status'] == 'verified' else 1)
