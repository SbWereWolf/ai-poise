from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from ..foundation.errors import PoiseError


@dataclass(frozen=True)
class BackupEntry:
    name: str
    created_at: str


@dataclass(frozen=True)
class BackupCopy:
    name: str
    live_size_bytes: int
    backup_size_bytes: int


def exact_backup_name(value: str) -> str:
    if not isinstance(value, str) or value in ("", ".", "..") or "/" in value or "\\" in value:
        raise PoiseError("Backup name must be one exact filename component")
    return value


def generated_backup_name(database_name: str, observed_at) -> str:
    exact_backup_name(database_name)
    offset = observed_at.utcoffset()
    if observed_at.tzinfo is None or offset is None:
        raise PoiseError("Backup clock must return a timezone-aware timestamp")
    dot = database_name.rfind(".")
    stem = database_name[:dot] if dot > 0 else database_name
    suffix = database_name[dot:] if dot > 0 else ""
    stamp = (observed_at - offset).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stem}-backup-{stamp}{suffix}"


RETENTION_SECONDS = 23 * 60 * 60
RETENTION_SCHEMA = 'poise-backup-retention-1'


def retention_input_digest(grant: dict) -> str:
    value = {key: grant[key] for key in (
        'project', 'backup_name', 'sha256', 'valid_until', 'authorization', 'request_id')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def admit_decision(decision, *, reference, project, name, digest, deadline, instant):
    if (not isinstance(decision, dict) or decision.get('role') != 'user'
            or not isinstance(decision.get('text'), str) or not decision['text'].strip()
            or decision.get('project') != project or decision.get('backup_name') != name
            or decision.get('sha256') != digest
            or instant(decision.get('valid_until')) != deadline):
        raise PoiseError('Authorization decision does not match exact project/name/digest/deadline scope')
    if not isinstance(reference, str) or not reference.strip():
        raise PoiseError('An actual recorded authorization source reference is required')
    return {**decision, 'reference': reference}


def validate_grant(grant, *, project, name, instant):
    try:
        if (not isinstance(grant, dict) or grant.get('schema') != RETENTION_SCHEMA
                or grant['project'] != project or grant['backup_name'] != name
                or not isinstance(grant['request_id'], str) or not grant['request_id']
                or not isinstance(grant['actor'], str) or not grant['actor']
                or not isinstance(grant['sha256'], str) or len(grant['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in grant['sha256'])
                or grant['input_digest'] != retention_input_digest(grant)):
            raise ValueError('invalid grant identity/checksum')
        deadline = instant(grant['valid_until'])
        admit_decision(grant['authorization'], reference=grant['authorization']['reference'],
                       project=project, name=name, digest=grant['sha256'], deadline=deadline, instant=instant)
        return deadline
    except (ValueError, TypeError, KeyError) as exc:
        raise PoiseError('Retention metadata is invalid; reconcile the exact recorded decision') from exc
