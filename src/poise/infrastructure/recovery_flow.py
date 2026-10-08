"""Configured workspace-recover execution; terminal facts, never guessed retries."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess

from ..common import PoiseError, digest, encoded, file_digest
from .goal_config import atomic_write
from .locking import exclusive_lock


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
    def __init__(self, tool_argv, file_mode, lock_seconds, lock_poll_seconds):
        self.argv = tool_argv
        self.mode = file_mode
        self.wait = lock_seconds
        self.poll = lock_poll_seconds

    @staticmethod
    def _facts(path):
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise PoiseError('Recovery invocation proof is not a regular file')
        return {'size': path.stat().st_size, 'digest': file_digest(path)}

    def run(self, flow, variables, manifest, session):
        prepared = materialize(flow, variables)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        journal = manifest.with_suffix('.launch.json')
        with exclusive_lock(manifest.with_suffix('.launch.lock'), self.wait, self.poll):
            return self._run(prepared, manifest, session, journal)

    def _run(self, prepared, manifest, session, journal):
        identity = digest({'argv': self.argv, 'flow': prepared,
                           'manifest': str(manifest.absolute()), 'session': str(session.absolute())})
        unknown = {'status': 'unknown', 'steps': [], 'reason': 'unknown_external_outcome'}
        manifest_exists = manifest.exists()
        if manifest_exists and json.loads(manifest.read_text()) != prepared:
            raise PoiseError('Saved recovery flow differs from configured request')
        proof_paths = [manifest.with_suffix('.stdout.txt'), manifest.with_suffix('.stderr.txt'),
                       session / 'state.json']
        if journal.exists():
            try:
                saved = json.loads(journal.read_text())
                if saved['schema'] != 'poise-recovery-launch-1' or saved['identity'] != identity:
                    raise PoiseError('Recovery invocation identity differs from configured request')
                if saved['phase'] != 'terminal':
                    return unknown
                terminal = saved['terminal']
                if terminal['proofs'] != [self._facts(path) for path in proof_paths]:
                    return unknown
                return terminal['outcome']
            except (OSError, ValueError, KeyError, TypeError):
                return unknown
        # Tool state cannot prove an owner-observed process termination.
        if manifest_exists or (session / 'state.json').exists():
            return unknown
        atomic_write(manifest, (encoded(prepared) + '\n').encode(), self.mode)
        launch = {'schema': 'poise-recovery-launch-1', 'identity': identity,
                  'phase': 'launched', 'terminal': None}
        atomic_write(journal, (encoded(launch) + '\n').encode(), self.mode)
        spawned, returncode = False, None
        try:
            with proof_paths[0].open('wb') as out, proof_paths[1].open('wb') as err:
                process = subprocess.Popen([*self.argv, 'flow', 'run', '--manifest', str(manifest),
                                            '--session', str(session)], stdout=out, stderr=err)
                spawned = True
                returncode = process.wait()
        except OSError as exc:
            if spawned:
                return unknown
            result = {'status': 'failed', 'steps': [], 'reason': str(exc)}
        else:
            result = self.inspect_terminal(prepared, session)
            result['tool_exit_code'] = returncode
            if result['status'] == 'complete' and returncode != 0:
                result.update(status='failed', reason='tool_exit_nonzero')
        launch.update(phase='terminal', terminal={
            'spawned': spawned, 'returncode': returncode, 'outcome': result,
            'proofs': [self._facts(path) for path in proof_paths]})
        atomic_write(journal, (encoded(launch) + '\n').encode(), self.mode)
        return result

    @staticmethod
    def inspect_terminal(flow, session):
        try:
            state = json.loads((session / 'state.json').read_text())
        except (OSError, ValueError):
            return {'status': 'unknown', 'steps': [], 'reason': 'missing_terminal_flow_receipt'}
        raw_results = state.get('results')
        if not isinstance(raw_results, list) or any(not isinstance(r, dict) for r in raw_results):
            return {'status': 'unknown', 'steps': [], 'reason': 'invalid_terminal_flow_receipt'}
        results = [{**r, 'exit_code': r.get('exitCode')} for r in raw_results]
        expected_ids = [step['id'] for step in flow['steps']]
        actual_ids = [r.get('id') for r in results]
        if (len(expected_ids) != len(set(expected_ids))
                or actual_ids != expected_ids[:len(actual_ids)]):
            return {'status': 'unknown', 'steps': results, 'reason': 'flow_step_identity_differs'}
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
