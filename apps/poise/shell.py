"""Standalone Session entry: explicit configuration, bounded packet and response."""
import json
import os
from pathlib import Path
import sys

from application.sessions import SessionOperations
from application.hooks import HookEvents
from application.interception import HookInterception
from application.capabilities import CapabilityChecks
from application.state import StateOperations
from domain.generic import Problem, exact_fields, invalid
from domain.session_config import parse_settings
from domain.state import StateSettings, invalid_config
from infrastructure.callers import CallerResolver
from infrastructure.json_io import loads
from infrastructure.sessions import SessionRepository
from infrastructure.state import StateStore
from infrastructure.hooks import HookRegistry
from infrastructure.hook_configuration import load_hook_configuration
from infrastructure.commands import CommandObserver, LocalProbeExecutor
from infrastructure.files import FileSurface, OutputStore
from infrastructure.mcp import StdioProbe


SCHEMA = 'stride-poise/v1'


def load_config(path):
    try:
        value = loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        invalid_config('/config')
    return parse_settings(value), value


def read_packet(limit):
    remaining, data = limit + 1, bytearray()
    while remaining:
        chunk = sys.stdin.buffer.read(min(remaining, 65536))
        if not chunk:
            break
        data.extend(chunk)
        remaining -= len(chunk)
    if len(data) > limit:
        raise Problem('packet_limit', {'limit': 'packet_bytes'}, 'reduce:packet')
    try:
        return loads(data)
    except (ValueError, RecursionError):
        raise Problem('invalid_json', {}, 'provide:json') from None


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    output = None
    try:
        if len(arguments) != 2 or arguments[0] != '--config' or not arguments[1]:
            invalid_config('/config')
        path = Path(arguments[1]).absolute()
        settings, raw_config = load_config(path)
        packet = exact_fields(read_packet(settings.packet_bytes), {'schema', 'operation', 'input'}, '/')
        if packet['schema'] != SCHEMA:
            value = packet['schema'] if isinstance(packet['schema'], str) else None
            raise Problem('unsupported_schema', {'schema': value}, 'select:schema')
        operation = packet['operation']
        if operation not in ('session.ensure', 'session.assign_role', 'session.status', 'hook.event', 'hook.intercept'):
            invalid('/operation')
        if operation in ('session.ensure', 'session.status'):
            exact_fields(packet['input'], set(), '/input')
        state_settings = StateSettings(settings.session_store, 1, settings.packet_bytes,
                                       settings.sqlite_timeout_seconds)
        store = StateStore(state_settings, path)
        operations = SessionOperations(StateOperations(store, 1),
            CallerResolver(settings, path, os.environ), SessionRepository(store), settings.roles)
        if operation.startswith('hook.'):
            hooks = load_hook_configuration(raw_config, path, settings, operation)
            if operation == 'hook.event':
                result = HookEvents(settings, hooks.definition, operations,
                    HookRegistry(store, operations.repository), FileSurface(settings.packet_bytes)).handle(packet['input'])
            else:
                # Construction allocates no files; capture begins only after complete admission.
                generic = hooks.generic
                commands, capabilities, limits = None, None, None
                files = FileSurface(settings.packet_bytes)
                if generic is not None:
                    output = OutputStore(generic)
                    limits = generic['limits']
                    files = FileSurface(limits['input_bytes'])
                    commands = CommandObserver(output)
                    capabilities = CapabilityChecks(LocalProbeExecutor(commands, StdioProbe, output), files, limits)
                result = HookInterception(hooks.definition, operations, commands, capabilities,
                                         files, limits, hooks.probes).handle(packet['input'])
        elif operation == 'session.ensure':
            result = operations.ensure()
        elif operation == 'session.assign_role':
            result = operations.assign_role(packet['input'])
        else:
            result = operations.status()
        response = {'schema': SCHEMA, 'status': 'ok', 'result': result, 'error': None, 'evidence': [] if output is None else output.references}
        exit_code = 0
    except Problem as problem:
        response = {'schema': SCHEMA, 'status': problem.status, 'result': problem.result,
                    'error': problem.error, 'evidence': [] if output is None else output.references}
        exit_code = 1 if problem.status == 'unavailable' else 2
    except OSError as error:
        response = {'schema': SCHEMA, 'status': 'unavailable', 'result': None,
                    'error': {'code': 'io_unavailable', 'details': {'reason': str(error)},
                              'action': 'configure:files'},
                    'evidence': [] if output is None else output.references}
        exit_code = 1
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, allow_nan=False,
                            separators=(',', ':')) + '\n').encode('utf-8'))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
