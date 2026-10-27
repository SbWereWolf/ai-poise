"""Standalone generic capability composition, independent of Session lifecycle."""
from pathlib import Path
import json
import sys

from application.capabilities import CapabilityChecks
from application.context import GenericContext
from application.navigation import CodeNavigation
from application.skills import GenericSkills, InstructionPreparation
from domain.generic import Problem, exact_fields, invalid
from infrastructure.commands import CommandObserver, LocalProbeExecutor
from infrastructure.files import FileSurface, OutputStore
from infrastructure.json_io import loads
from infrastructure.generic_config import load_config
from infrastructure.mcp import StdioProbe


SCHEMA = 'poise-generic/v1'

def dispatch(operation, request, config, output):
    files = FileSurface(config['limits']['input_bytes'])
    commands = CommandObserver(output)
    probes = LocalProbeExecutor(commands, StdioProbe, output)
    skills = GenericSkills(files)
    operations = {
        'context': GenericContext(str(Path(__file__).resolve().parent), files, commands,
                                  config['limits']).read,
        'capabilities': CapabilityChecks(probes, files, config['limits']).run,
        'skills.list': skills.list,
        'skills.read': skills.read,
        'skills.prepare': InstructionPreparation(files, skills).prepare,
        'navigation': CodeNavigation(probes, files, config['limits']).run,
    }
    if not isinstance(operation, str) or operation not in operations:
        invalid('/operation')
    if not isinstance(request, dict):
        invalid('/input')
    return operations[operation](request)


def respond(status, result, error, evidence):
    value = {'schema': SCHEMA, 'status': status, 'result': result,
             'error': error, 'evidence': evidence}
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                      separators=(',', ':')).encode('utf-8') + b'\n')
    return {'ok': 0, 'unavailable': 1, 'needs_inputs': 1, 'rejected': 2}[status]


def main():
    config, output = None, None
    status, result, error = 'ok', None, None
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--config':
            raise Problem('invalid_config', {'path': '/config'}, 'correct:config')
        config = load_config(Path(sys.argv[2]).absolute())
        output = OutputStore(config)
        raw = sys.stdin.buffer.read(config['limits']['packet_bytes'] + 1)
        if len(raw) > config['limits']['packet_bytes']:
            raise Problem('packet_limit', {'limit': 'packet_bytes'}, 'reduce:packet')
        try:
            packet = loads(raw)
        except (ValueError, RecursionError):
            raise Problem('invalid_json', {}, 'provide:json') from None
        exact_fields(packet, {'schema', 'operation', 'input'}, '')
        if packet['schema'] != SCHEMA:
            declared = packet['schema'] if isinstance(packet['schema'], str) else None
            raise Problem('unsupported_schema', {'schema': declared}, 'select:schema')
        result = dispatch(packet['operation'], packet['input'], config, output)
    except Problem as exc:
        status, result, error = exc.status, exc.result, exc.error
    except OSError as exc:
        status = 'unavailable'
        error = {'code': 'io_unavailable', 'details': {'reason': str(exc)}, 'action': 'configure:files'}
    if result is not None and config is not None:
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > config['limits']['result_bytes']:
            try:
                output.save_result(result)
            except OSError as exc:
                error = {'code': 'io_unavailable', 'details': {'reason': str(exc)},
                         'action': 'configure:files'}
            else:
                error = {'code': 'result_limit', 'details': {'limit': 'result_bytes'}, 'action': 'read:result'}
            status, result = 'unavailable', None
    return respond(status, result, error, [] if output is None else output.references)


if __name__ == '__main__':
    raise SystemExit(main())
