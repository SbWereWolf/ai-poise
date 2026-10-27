"""Explicit standalone entry composition for Poise state preparation and reading."""
import json
from pathlib import Path
import sys

from application.state import StateOperations
from domain.generic import Problem, exact_fields, invalid
from domain.state import invalid_config, parse_settings
from infrastructure.json_io import loads
from infrastructure.state import StateStore


def main():
    status, result, error = 'ok', None, None
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--config':
            invalid_config('/config')
        config_path = Path(sys.argv[2]).absolute()
        try:
            settings = parse_settings(loads(config_path.read_bytes()))
        except (OSError, ValueError, RecursionError):
            invalid_config('/config')
        raw = sys.stdin.buffer.read(settings.packet_bytes + 1)
        if len(raw) > settings.packet_bytes:
            raise Problem('packet_limit', {'limit': 'packet_bytes'}, 'reduce:packet')
        try:
            packet = loads(raw)
        except (ValueError, RecursionError):
            raise Problem('invalid_json', {}, 'provide:json') from None
        exact_fields(packet, {'schema', 'operation', 'input'}, '')
        if packet['schema'] != 'poise-state/v1':
            declared = packet['schema'] if isinstance(packet['schema'], str) else None
            raise Problem('unsupported_schema', {'schema': declared}, 'select:schema')
        operation = packet['operation']
        if not isinstance(operation, str) or operation not in ('prepare', 'status'):
            invalid('/operation')
        exact_fields(packet['input'], set(), '/input')
        operations = StateOperations(StateStore(settings, config_path), settings.target_version)
        result = operations.prepare() if operation == 'prepare' else operations.status()
    except Problem as failure:
        status, result, error = failure.status, failure.result, failure.error
    response = {'schema': 'poise-state/v1', 'status': status, 'result': result,
                'error': error, 'evidence': []}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False, allow_nan=False,
                                      separators=(',', ':')).encode('utf-8') + b'\n')
    return {'ok': 0, 'unavailable': 1, 'rejected': 2}[status]


if __name__ == '__main__':
    raise SystemExit(main())
