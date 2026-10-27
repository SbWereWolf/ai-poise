"""Standalone generic capability composition, independent of Session lifecycle."""
from pathlib import Path
import json
import sys

from application.capabilities import CapabilityChecks
from application.context import GenericContext
from application.navigation import CodeNavigation
from application.skills import GenericSkills, InstructionPreparation
from domain.generic import (Problem, exact_fields, invalid, positive_count,
                            positive_duration)
from infrastructure.commands import CommandObserver, LocalProbeExecutor
from infrastructure.files import FileSurface, OutputStore
from infrastructure.json_io import loads
from infrastructure.mcp import StdioProbe


SCHEMA = 'poise-generic/v1'
COUNTS = {'packet_bytes', 'input_bytes', 'result_bytes', 'batch_count', 'provider_output_bytes'}


def config_fields(value, fields, pointer):
    if not isinstance(value, dict):
        raise Problem('invalid_config', {'path': pointer}, 'correct:config')
    missing = sorted(fields - value.keys())
    if missing:
        path = pointer + '/' + missing[0] if len(missing) == 1 else pointer
        raise Problem('invalid_config', {'path': path}, 'correct:config')
    return {key: value[key] for key in fields}


def load_config(path):
    try:
        raw = loads(path.read_bytes())
    except (OSError, ValueError):
        raise Problem('invalid_config', {'path': '/config'}, 'correct:config') from None
    config = config_fields(raw, {'schema', 'results_root', 'file_mode', 'files', 'limits'}, '')
    if config['schema'] != 'poise-generic-config/v1':
        raise Problem('invalid_config', {'path': '/schema'}, 'correct:config')
    limits = config_fields(config['limits'], COUNTS | {'provider_timeout_seconds'}, '/limits')
    for key, value in limits.items():
        valid = positive_duration(value) if key == 'provider_timeout_seconds' else positive_count(value)
        if not valid:
            raise Problem('invalid_config', {'path': '/limits/' + key}, 'correct:config')
    config['limits'] = limits
    files = config_fields(config['files'], {'stdout', 'stderr', 'receipt', 'result'}, '/files')
    for key, name in files.items():
        if not isinstance(name, str) or not name or Path(name).name != name or name in ('.', '..'):
            raise Problem('invalid_config', {'path': '/files/' + key}, 'correct:config')
    if len(set(files.values())) != len(files):
        raise Problem('invalid_config', {'path': '/files'}, 'correct:config')
    config['files'] = files
    if type(config['file_mode']) is not int or not 0 <= config['file_mode'] <= 0o777:
        raise Problem('invalid_config', {'path': '/file_mode'}, 'correct:config')
    if not isinstance(config['results_root'], str) or not config['results_root']:
        raise Problem('invalid_config', {'path': '/results_root'}, 'correct:config')
    root = Path(config['results_root'])
    config['results_root'] = str((root if root.is_absolute() else path.parent / root).resolve())
    return config


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
            output.save_result(result)
            status, result = 'unavailable', None
            error = {'code': 'result_limit', 'details': {'limit': 'result_bytes'}, 'action': 'read:result'}
    return respond(status, result, error, [] if output is None else output.references)


if __name__ == '__main__':
    raise SystemExit(main())
