from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from ..common import configured_root, descendant, read_json
from ..modules.backups.domain import BackupCopy, BackupEntry
from ..modules.foundation.errors import PoiseError


def _read_only_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(_read_only_uri(path), uri=True)) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise PoiseError(f"Cannot open or read SQLite database {path.name}: {exc}") from exc
    if rows != [("ok",)]:
        raise PoiseError(f"SQLite integrity_check failed for {path.name}")


def _sync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_pending_name(name: str) -> bool:
    return name.startswith(".") and name.endswith(".pending")


class LocalTaskDatabaseBackups:
    def __init__(self, config_path: Path):
        path = Path(config_path).resolve()
        root, config = path.parent, read_json(path)
        try:
            paths = config["paths"]
            state = configured_root(root, paths["state"])
            self.database = descendant(state, paths["database"])
        except (KeyError, TypeError) as exc:
            raise PoiseError(f"Incomplete project manifest for Task DB backup: {exc}") from exc
        self.directory = self.database.parent / "backups"

    @property
    def database_name(self) -> str:
        return self.database.name

    def list_entries(self) -> tuple[BackupEntry, ...]:
        if not self.directory.exists():
            return ()
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise PoiseError("Backup directory must be a regular directory")
        entries = []
        try:
            for path in self.directory.iterdir():
                if _is_pending_name(path.name) or path.is_symlink() or not path.is_file():
                    continue
                created_at = datetime.fromtimestamp(path.stat().st_ctime, timezone.utc).isoformat()
                entries.append(BackupEntry(path.name, created_at))
        except OSError as exc:
            raise PoiseError(f"Cannot list backup directory: {exc}") from exc
        return tuple(sorted(entries, key=lambda item: item.name))

    def create_copy(self, name: str) -> BackupCopy:
        destination = self.directory / name
        temporary = None
        try:
            if self.database.is_symlink() or not self.database.is_file():
                raise PoiseError(f"Configured live Task DB is missing or not a regular file: {self.database}")
            live_size = self.database.stat().st_size
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.directory.is_symlink() or not self.directory.is_dir():
                raise PoiseError("Backup directory must be a regular directory")
            if destination.exists() or destination.is_symlink():
                raise PoiseError(f"Backup already exists; filename collision: {name}")
            with tempfile.NamedTemporaryFile(
                dir=self.directory,
                prefix=f".{name}.",
                suffix=".pending",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
            with closing(sqlite3.connect(_read_only_uri(self.database), uri=True)) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                    target.commit()
            _integrity(temporary)
            _sync_file(temporary)
            backup_size = temporary.stat().st_size
            os.replace(temporary, destination)
            temporary = None
            try:
                _sync_directory(self.directory)
            except OSError as exc:
                raise PoiseError(
                    f"Backup was published as {name}, but directory sync failed; "
                    f"run `poise backup list` and check SQLite integrity before retrying: {exc}"
                ) from exc
            return BackupCopy(name, live_size, backup_size)
        except PoiseError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PoiseError(f"SQLite database backup failed for {self.database.name}: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def restore_copy(self, name: str) -> None:
        selected = self.directory / name
        temporary = None
        try:
            if _is_pending_name(name):
                raise PoiseError(f"Backup name belongs to an internal pending file: {name}")
            if self.directory.is_symlink() or not self.directory.is_dir():
                raise PoiseError(
                    f"Backup is missing or not found because the backup directory is not a regular directory: {name}"
                )
            if selected.is_symlink():
                raise PoiseError(f"Backup must be a regular file, not a symlink: {name}")
            if not selected.is_file():
                raise PoiseError(f"Backup is missing or not found: {name}")
            if not os.access(selected, os.R_OK):
                raise PoiseError(f"Cannot read backup; check file permission: {name}")
            _integrity(selected)
            if self.database.is_symlink() or not self.database.is_file():
                raise PoiseError(f"Configured live Task DB is missing or not a regular file: {self.database}")
            mode = stat.S_IMODE(self.database.stat().st_mode)
            with tempfile.NamedTemporaryFile(
                dir=self.database.parent,
                prefix=f".{self.database.name}.restore-",
                suffix=".pending",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
            with closing(sqlite3.connect(_read_only_uri(selected), uri=True)) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                    target.commit()
            _integrity(temporary)
            temporary.chmod(mode)
            _sync_file(temporary)
            os.replace(temporary, self.database)
            temporary = None
            try:
                _sync_directory(self.database.parent)
            except OSError as exc:
                raise PoiseError(
                    f"Live Task DB was replaced from {name}, but directory sync failed; "
                    f"keep writers stopped and run SQLite PRAGMA integrity_check before retrying: {exc}"
                ) from exc
        except PoiseError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PoiseError(f"Cannot restore SQLite backup {name}: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
