"""Stable caller identity is independent from workflow role and authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from ..foundation.errors import DomainError


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f'{name} required')
    return value


def _session(value, name):
    value = _required(value, name)
    if value in ('.', '..') or '/' in value or '\\' in value or '\x00' in value:
        raise DomainError(f'{name} must be one path component')
    return value


def _key(project, kind, value):
    data = [_required(project, 'project'), kind, _required(value, 'caller identity')]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class CallerIdentity:
    key: str
    native_session: str | None

    @classmethod
    def native(cls, project, native_session):
        native_session = _session(native_session, 'native session')
        return cls(_key(project, 'native', native_session), native_session)

    @classmethod
    def generated(cls, project, caller_binding, native_session=None):
        if native_session is not None:
            native_session = _session(native_session, 'native session')
        return cls(_key(project, 'generated', caller_binding), native_session)


@dataclass(frozen=True)
class EstablishedSession:
    session_id: str
    origin: str

