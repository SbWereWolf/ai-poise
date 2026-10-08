"""Configured workspace-recover execution; terminal facts, never guessed retries."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess

from ..common import PoiseError


_VARIABLE = re.compile(r'\$\{variables\.([a-zA-Z_][a-zA-Z_0-9]*)\}')


def materialize(value, variables):
    if isinstance(value, list):
        return [materialize(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: materialize(item, variables) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    match = _VARIABLE.fullmatch(value)
    if match:
        if match[1] not in variables: raise PoiseError('Missing flow variable: ' + match[1])
        return deepcopy(variables[match[1]])
    def replace(match):
        if match[1] not in variables: raise PoiseError('Missing flow variable: ' + match[1])
        item = variables[match[1]]
        if not isinstance(item, str): raise PoiseError('Non-string embedded flow variable')
        return item
    return _VARIABLE.sub(replace, value)


class WorkspaceRecoveryFlow:
    def __init__(self, tool_argv):
        self.argv = tool_argv

    def run(self, flow, variables, manifest, session):
        prepared = materialize(flow, variables)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + '\n')
        # An existing running session is evidence of uncertain effects, not retry permission.
        if (session / 'state.json').exists():
            return self.inspect_terminal(prepared, session)
        try:
            with manifest.with_suffix('.stdout.txt').open('wb') as out, manifest.with_suffix('.stderr.txt').open('wb') as err:
                completed = subprocess.run([*self.argv, 'flow', 'run', '--manifest', str(manifest),
                                            '--session', str(session)], stdout=out, stderr=err)
        except OSError as exc:
            return {'status': 'failed', 'steps': [], 'reason': str(exc)}
        result = self.inspect_terminal(prepared, session)
        result['tool_exit_code'] = completed.returncode
        return result

    @staticmethod
    def inspect_terminal(flow, session):
        try:
            state = json.loads((session / 'state.json').read_text())
        except (OSError, ValueError):
            return {'status': 'unknown', 'steps': [], 'reason': 'missing_terminal_flow_receipt'}
        results = [{**r, 'exit_code': r.get('exitCode')} for r in state['results']]
        if state.get('inflight') is not None:
            return {'status': 'unknown', 'steps': results, 'reason': 'unknown_external_outcome'}
        if state.get('pending'):
            pending = state['pending']
            results.append({'id': pending['stepId'], 'status': 'failed', 'exit_code': None,
                            'error': pending['observation']['message']})
        success = (state['status'] in ('COMPLETED', 'COMPLETED_WITH_OBSERVATIONS')
                   and len(results) == len(flow['steps']) and all(r['status'] == 'passed' for r in results))
        return {'status': 'complete' if success else 'failed', 'steps': results,
                'reason': None if success else 'delivery_step_failed'}
