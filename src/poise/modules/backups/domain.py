from __future__ import annotations

from dataclasses import dataclass

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
