"""Explicit settings for Poise-owned state, independent of storage and Tasks."""
from dataclasses import dataclass
import math

from domain.generic import Problem, positive_count


@dataclass(frozen=True)
class StateSettings:
    database: str
    target_version: int
    packet_bytes: int
    sqlite_timeout_seconds: float


def invalid_config(path):
    raise Problem('invalid_config', {'path': path}, 'correct:config')


def required(value, fields, path):
    if not isinstance(value, dict):
        invalid_config(path)
    for key in sorted(fields):
        if key not in value:
            invalid_config(path + '/' + key)
    return value


def parse_settings(value):
    config = required(value, {'schema', 'database', 'target_version', 'limits'}, '')
    if config['schema'] != 'poise-state-config/v1':
        invalid_config('/schema')
    database = config['database']
    if not isinstance(database, str) or not database or '\0' in database:
        invalid_config('/database')
    if type(config['target_version']) is not int or config['target_version'] != 1:
        invalid_config('/target_version')
    limits = required(config['limits'], {'packet_bytes', 'sqlite_timeout_seconds'}, '/limits')
    if not positive_count(limits['packet_bytes']):
        invalid_config('/limits/packet_bytes')
    timeout = limits['sqlite_timeout_seconds']
    try:
        valid_timeout = type(timeout) in (int, float) and timeout >= 0 and math.isfinite(timeout)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        invalid_config('/limits/sqlite_timeout_seconds')
    return StateSettings(database, config['target_version'], limits['packet_bytes'], timeout)
