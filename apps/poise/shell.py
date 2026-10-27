"""Standalone Session entry: explicit configuration, bounded packet and response."""
import json
import os
from pathlib import Path
import sys

from application.sessions import SessionOperations
from application.state import StateOperations
from domain.generic import Problem, exact_fields, invalid
from domain.session_config import parse_settings
from domain.state import StateSettings, invalid_config
from infrastructure.callers import CallerResolver
from infrastructure.json_io import loads
from infrastructure.sessions import SessionRepository
from infrastructure.state import StateStore


SCHEMA = 'stride-poise/v1'


def load_config(path):
    try:
        value = loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        invalid_config('/config')
    return parse_settings(value)


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
    try:
        if len(arguments) != 2 or arguments[0] != '--config' or not arguments[1]:
            invalid_config('/config')
        path = Path(arguments[1]).absolute()
        settings = load_config(path)
        packet = exact_fields(read_packet(settings.packet_bytes), {'schema', 'operation', 'input'}, '/')
        if packet['schema'] != SCHEMA:
            value = packet['schema'] if isinstance(packet['schema'], str) else None
            raise Problem('unsupported_schema', {'schema': value}, 'select:schema')
        operation = packet['operation']
        if operation not in ('session.ensure', 'session.assign_role', 'session.status'):
            invalid('/operation')
        if operation != 'session.assign_role':
            exact_fields(packet['input'], set(), '/input')
        state_settings = StateSettings(settings.session_store, 1, settings.packet_bytes,
                                       settings.sqlite_timeout_seconds)
        store = StateStore(state_settings, path)
        operations = SessionOperations(StateOperations(store, 1),
            CallerResolver(settings, path, os.environ), SessionRepository(store), settings.roles)
        if operation == 'session.ensure':
            result = operations.ensure()
        elif operation == 'session.assign_role':
            result = operations.assign_role(packet['input'])
        else:
            result = operations.status()
        response = {'schema': SCHEMA, 'status': 'ok', 'result': result, 'error': None, 'evidence': []}
        exit_code = 0
    except Problem as problem:
        response = {'schema': SCHEMA, 'status': problem.status, 'result': problem.result,
                    'error': problem.error, 'evidence': []}
        exit_code = 1 if problem.status == 'unavailable' else 2
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, allow_nan=False,
                            separators=(',', ':')) + '\n').encode('utf-8'))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
