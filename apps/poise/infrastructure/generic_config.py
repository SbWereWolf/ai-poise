"""Explicit generic filesystem settings shared by standalone composition roots."""
from pathlib import Path

from domain.generic import Problem, positive_count, positive_duration
from infrastructure.json_io import loads


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
        if (not isinstance(name, str) or not name or '\0' in name
                or Path(name).name != name or name in ('.', '..')):
            raise Problem('invalid_config', {'path': '/files/' + key}, 'correct:config')
    if len(set(files.values())) != len(files):
        raise Problem('invalid_config', {'path': '/files'}, 'correct:config')
    config['files'] = files
    if type(config['file_mode']) is not int or not 0 <= config['file_mode'] <= 0o777:
        raise Problem('invalid_config', {'path': '/file_mode'}, 'correct:config')
    if (not isinstance(config['results_root'], str) or not config['results_root']
            or '\0' in config['results_root']):
        raise Problem('invalid_config', {'path': '/results_root'}, 'correct:config')
    root = Path(config['results_root'])
    config['results_root'] = str((root if root.is_absolute() else path.parent / root).resolve())
    return config

