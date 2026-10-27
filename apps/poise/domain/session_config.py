"""Explicit standalone Session settings without filesystem or environment effects."""
from dataclasses import dataclass
import math

from domain.generic import positive_count
from domain.state import invalid_config, required


@dataclass(frozen=True)
class SessionSettings:
    installation_id: str
    session_store: str
    caller_mode: str
    binding_file: str | None
    roles: tuple[str, ...]
    packet_bytes: int
    sqlite_timeout_seconds: float


def configured_text(value, path):
    if not isinstance(value, str) or not value or '\0' in value:
        invalid_config(path)
    return value


def parse_settings(value):
    config = required(value, {'schema', 'installation_id', 'session_store',
                              'caller', 'roles', 'limits'}, '')
    if config['schema'] != 'stride-poise-config/v1':
        invalid_config('/schema')
    installation = configured_text(config['installation_id'], '/installation_id')
    store = configured_text(config['session_store'], '/session_store')
    caller = required(config['caller'], {'mode'}, '/caller')
    if caller['mode'] not in ('native', 'generated'):
        invalid_config('/caller/mode')
    binding = None
    if caller['mode'] == 'generated':
        required(caller, {'binding_file'}, '/caller')
        binding = configured_text(caller['binding_file'], '/caller/binding_file')
    roles = config['roles']
    if not isinstance(roles, list) or not roles:
        invalid_config('/roles')
    for role in roles:
        configured_text(role, '/roles')
    if len(set(roles)) != len(roles):
        invalid_config('/roles')
    limits = required(config['limits'], {'packet_bytes', 'sqlite_timeout_seconds'}, '/limits')
    if not positive_count(limits['packet_bytes']):
        invalid_config('/limits/packet_bytes')
    timeout = limits['sqlite_timeout_seconds']
    try:
        valid = type(timeout) in (int, float) and timeout >= 0 and math.isfinite(timeout)
    except OverflowError:
        valid = False
    if not valid:
        invalid_config('/limits/sqlite_timeout_seconds')
    return SessionSettings(installation, store, caller['mode'], binding,
                           tuple(roles), limits['packet_bytes'], timeout)
