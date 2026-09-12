"""Composition helpers for the common caller-to-session boundary."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import uuid

from ..application.session_establishment import SessionEstablisher
from ..common import PoiseError, configured_root, descendant, load_config
from ..modules.session_establishment.domain import CallerIdentity, EstablishedSession
from ..runtime import Poise
from .sqlite.database import Database
from .sqlite.runtime_adapter import RuntimeRegistry


@dataclass(frozen=True)
class EstablishedPoise:
    runtime: Poise
    registry: RuntimeRegistry
    session: EstablishedSession


def _binding_id(path):
    target = Path(path)
    if not target.is_absolute() or target.is_symlink():
        raise PoiseError('POISE_CALLER_BINDING must be an absolute regular-file path')
    parent = target.parent.resolve(strict=True)
    if parent != target.parent:
        raise PoiseError('POISE_CALLER_BINDING must not contain symlinked path components')

    def read():
        try:
            value = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PoiseError('POISE_CALLER_BINDING is unreadable or invalid') from exc
        if (not isinstance(value, dict) or set(value) != {'schema', 'caller_id'}
                or value['schema'] != 'poise-caller-binding-1'
                or not isinstance(value['caller_id'], str) or not value['caller_id']):
            raise PoiseError('POISE_CALLER_BINDING has an unsupported document')
        return value['caller_id']

    if target.exists():
        if not target.is_file():raise PoiseError('POISE_CALLER_BINDING must be a regular file')
        return read()
    caller_id = uuid.uuid4().hex
    raw = (json.dumps({'schema':'poise-caller-binding-1','caller_id':caller_id}, separators=(',',':'))+'\n').encode()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read()
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        try:target.unlink()
        except OSError:pass
        raise
    return caller_id


def direct_caller(project, environ):
    native = environ.get('CODEX_SESSION_ID') or environ.get('CODEX_THREAD_ID')
    binding = environ.get('POISE_CALLER_BINDING')
    if binding:
        return CallerIdentity.generated(project, _binding_id(binding), native_session=native)
    if native:
        return CallerIdentity.native(project, native)
    raise PoiseError('No native caller identity; set POISE_CALLER_BINDING to an absolute persistent file')


def establish_poise(config_path, caller, inventory, clock, preferred_session=None):
    config_path = Path(config_path).resolve()
    root,cfg,_ = load_config(config_path)
    state = configured_root(root,cfg['paths']['state'])
    database = Database(descendant(state,cfg['paths']['database']),descendant(state,cfg['paths']['lock']),
                        cfg['limits']['lock_seconds'],cfg['limits']['lock_poll_seconds'])
    registry = RuntimeRegistry(database)
    session = SessionEstablisher(registry).establish(caller,inventory,preferred_session)
    return EstablishedPoise(Poise(config_path,session.session_id,clock),registry,session)
