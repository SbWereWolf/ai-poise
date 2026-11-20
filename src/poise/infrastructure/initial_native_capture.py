"""One complete native callback execution through the common registered runner."""
import os
from pathlib import Path
import uuid

from ..common import PoiseError
from ..execution import RegisteredCheckRunner, RegisteredLaunchFailure
from .goal_config import strict_json


def capture_native_callback(settings, position, handler, source_path, directory,
                            cleanup_seconds, *, parse_response=True, launch_observer=None):
    out, err = directory / 'stdout', directory / 'stderr'
    runner = RegisteredCheckRunner(settings.raw['output_chars'])
    exception = None
    try:
        with source_path.open('rb') as source:
            execution = runner.run(uuid.uuid4().hex, ['/bin/sh', '-c', handler['command']],
                Path.cwd(), dict(os.environ), handler['timeout'], out, err,
                cleanup_seconds=cleanup_seconds, stdin_file=source,
                launch_observer=launch_observer)
    except (RegisteredLaunchFailure, OSError) as exc:
        exception = {'type': type(exc).__name__, 'message': str(exc),
                     'cause': None if exc.__cause__ is None else str(exc.__cause__)}
        execution = {'actual_exit_code': None, 'stdout': str(out), 'stderr': str(err),
            'capture_complete': isinstance(exc, RegisteredLaunchFailure) and out.is_file() and err.is_file(),
            'exception': exception}
    stdout = out.read_bytes() if out.exists() else b''
    stderr = err.read_bytes() if err.exists() else b''
    failed = (exception is not None or execution['actual_exit_code'] != 0
              or execution.get('timed_out', False) or not execution['capture_complete'])
    response, parse_error = None, None
    if parse_response and (stdout or not failed):
        try:
            response = strict_json(stdout.decode('utf-8'))
            if not isinstance(response, dict):
                raise PoiseError('Native member response must be an object')
        except (PoiseError, ValueError, UnicodeError) as exc:
            response, parse_error, failed = None, str(exc), True
    diagnostic = stderr
    if exception:
        diagnostic += (exception['type'] + ': ' + exception['message'] + '\n').encode()
    elif parse_error:
        diagnostic += ('Invalid native member response: ' + parse_error + '\n').encode()
    elif execution.get('timed_out') and not diagnostic:
        diagnostic = b'Native member timed out\n'
    member = {'position': position, 'command': handler['command'], 'execution': execution,
              'response': response, 'parse_error': parse_error}
    outcome = {'exit_code': execution['actual_exit_code'], 'failed': failed,
               'response': response, 'stderr': diagnostic}
    return member, outcome
