from __future__ import annotations

from typing import Protocol

from .domain import BackupCopy, BackupEntry


class TaskDatabaseBackups(Protocol):
    @property
    def database_name(self) -> str: ...

    def list_entries(self) -> tuple[BackupEntry, ...]: ...

    def create_copy(self, name: str) -> BackupCopy: ...

    def restore_copy(self, name: str) -> None: ...
