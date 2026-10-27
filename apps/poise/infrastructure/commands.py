"""One configured synchronous observation, separated from Task check execution."""
import json
import os
from pathlib import Path
import signal
import subprocess

from domain.generic import Problem
from domain.probes import bind_workspace, satisfies
from infrastructure.json_io import loads


class CommandObserver:
    def __init__(self, output):
        self.output = output

    def run(self, argv, cwd, environment, value, timeout, maximum, identity):
        paths = self.output.capture_paths()
        timed_out, launch_error, code = False, None, None
        payload = b'' if value is None else json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n'
        with paths['stdout'].open('wb') as stdout, paths['stderr'].open('wb') as stderr:
            try:
                child = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                                         stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    child.communicate(payload, timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if child.poll() is None:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    child.communicate()
                code = child.returncode
            except OSError as exc:
                launch_error = str(exc)
        self.output.evidence(paths['stdout'], 'stdout')
        self.output.evidence(paths['stderr'], 'stderr')
        result = {'argv': argv, 'cwd': cwd, 'environment': environment,
                  'input': value, 'exit_code': code, 'timed_out': timed_out,
                  'timeout_seconds': timeout, 'max_output_bytes': maximum,
                  'launch_error': launch_error, 'stdout': str(paths['stdout']),
                  'stderr': str(paths['stderr']), 'receipt': str(paths['receipt'])}
        self.output.save_receipt(paths, result)
        if timed_out:
            raise Problem('provider_timeout', {'id': identity, 'limit': timeout},
                          'adjust:provider', 'unavailable')
        if launch_error:
            raise Problem('provider_unavailable', {'id': identity, 'reason': launch_error},
                          'configure:provider', 'unavailable')
        if paths['stdout'].stat().st_size + paths['stderr'].stat().st_size > maximum:
            raise Problem('provider_output_limit', {'id': identity, 'limit': maximum},
                          'adjust:provider', 'unavailable')
        return result


    def response(self, call):
        return loads(Path(call['stdout']).read_bytes())


class LocalProbeExecutor:
    def __init__(self, commands, mcp, output):
        self.commands, self.mcp, self.output = commands, mcp, output

    def observe(self, spec, workspace):
        probe = bind_workspace(spec.data, workspace)
        result = {'id': probe['id'], 'status': 'unavailable', 'tool_ref': probe['tool_ref'],
                  'project_path': workspace if probe['project_bound'] else None,
                  'version': None, 'observation': 'probe_failed', 'tools': [],
                  'receipt': None, 'remediation': probe['remediation'], 'reason': None}
        before = len(self.output.references)
        try:
            if probe['kind'] == 'command':
                call = self.commands.run(probe['argv'], probe['cwd'], probe['environment'], None,
                                         probe['timeout_seconds'], probe['max_output_bytes'], probe['id'])
                result['receipt'] = call['receipt']
                stdout = Path(call['stdout']).read_bytes().decode('utf-8')
                stderr = Path(call['stderr']).read_bytes().decode('utf-8')
                if call['exit_code'] != probe['expected_exit_code']:
                    raise ValueError('Actual command exit did not match')
                if not all(v in stdout for v in probe['stdout_contains']) or not all(
                        v in stderr for v in probe['stderr_contains']):
                    raise ValueError('Actual output did not match declared predicates')
                if probe['json_assertions'] and not satisfies(loads(stdout.encode('utf-8')),
                                                             probe['json_assertions']):
                    raise ValueError('Actual JSON observation did not match')
                result['observation'] = 'command_executed'
            else:
                call = self.mcp(probe, self.output).run()
                result['receipt'] = call['receipt']
                if probe['json_assertions'] and not satisfies(call['smoke'], probe['json_assertions']):
                    raise ValueError('Actual MCP observation did not match')
                result.update(observation=call['observation'], tools=call['tools'],
                              version=call['server']['version'])
            result['status'] = 'available'
        except Problem as exc:
            result['reason'] = str(exc)
            result['error'] = exc.error
        except (OSError, ValueError) as exc:
            result['reason'] = str(exc)
        if result['receipt'] is None:
            references = self.output.references[before:]
            result['receipt'] = next((v['path'] for v in references if v['kind'] == 'receipt'), None)
        return result
