from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import zlib

from ..common import configured_root, descendant, read_json
from ..modules.backups.domain import BackupCopy, BackupEntry
from ..modules.foundation.errors import PoiseError
from .task_materials import FileTaskMaterials
from .locking import blocking_directory_lock


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
        self.project_identity = config['project']
        try:
            paths = config["paths"]
            state = configured_root(root, paths["state"])
            self.database = descendant(state, paths["database"])
        except (KeyError, TypeError) as exc:
            raise PoiseError(f"Incomplete project manifest for Task DB backup: {exc}") from exc
        self.directory = self.database.parent / "backups"
        self.materials = (
            FileTaskMaterials(state, paths, config['project'], config).project_root()
            if 'task_materials' in paths else None
        )

    @property
    def filename_source(self) -> str:
        """Filename seed for the explicitly configured snapshot representation."""
        return self.database.with_suffix('.zip').name if self.materials is not None else self.database.name

    def list_entries(self) -> tuple[BackupEntry, ...]:
        if not self.directory.exists():
            return ()
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise PoiseError("Backup directory must be a regular directory")
        entries = []
        try:
            for path in self.directory.iterdir():
                if (_is_pending_name(path.name) or path.name.endswith('.retention.json')
                        or path.is_symlink() or not path.is_file()):
                    continue
                created_at = datetime.fromtimestamp(path.stat().st_ctime, timezone.utc).isoformat()
                entries.append(BackupEntry(path.name, created_at))
        except OSError as exc:
            raise PoiseError(f"Cannot list backup directory: {exc}") from exc
        return tuple(sorted(entries, key=lambda item: item.name))

    def create_copy(self, name: str) -> BackupCopy:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PoiseError(f'Cannot prepare backup resource: {exc}') from exc
        with self.locked():
            return self._create_copy(name)

    def _create_copy(self, name: str) -> BackupCopy:
        if self.materials is not None:
            return self._create_bundle(name)
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
            self._snapshot(self.database, temporary)
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

    def _selected(self, name: str) -> Path:
        selected = self.directory / name
        try:
            if _is_pending_name(name) or name.endswith('.retention.json'):
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
            return selected
        except OSError as exc:
            raise PoiseError(f"Cannot read backup {name}; check its permissions and retry: {exc}") from exc

    @contextmanager
    def _staged_database(self, source):
        temporary = None
        try:
            if self.database.is_symlink() or (self.database.exists() and not self.database.is_file()):
                raise PoiseError(f"Configured live Task DB is missing or not a regular file: {self.database}")
            mode = stat.S_IMODE((self.database if self.database.exists() else source).stat().st_mode)
            with tempfile.NamedTemporaryFile(
                dir=self.database.parent,
                prefix=f".{self.database.name}.restore-",
                suffix=".pending",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
            self._snapshot(source, temporary)
            temporary.chmod(mode)
            _sync_file(temporary)
            yield temporary
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _snapshot(source, destination):
        with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_db:
            with closing(sqlite3.connect(destination)) as target:
                source_db.backup(target)
                target.commit()
        _integrity(destination)

    def _create_bundle(self, name):
        published = False
        pending = None
        try:
            if self.database.is_symlink() or not self.database.is_file():
                raise PoiseError(f"Configured live Task DB is missing or not a regular file: {self.database}")
            live_size = self.database.stat().st_size
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.directory.is_symlink() or not self.directory.is_dir():
                raise PoiseError('Backup directory must be a regular directory')
            destination = self.directory / name
            if destination.exists() or destination.is_symlink():
                raise PoiseError(f'Backup already exists; filename collision: {name}')
            with tempfile.TemporaryDirectory(dir=self.directory, prefix=f'.{name}.', suffix='.pending') as pending:
                stage = Path(pending)
                self._snapshot(self.database, stage / 'task.sqlite')
                archive = stage / 'bundle.zip'
                with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                    bundle.write(stage / 'task.sqlite', 'task.sqlite')
                    bundle.writestr('task-materials/', b'')
                    try:
                        material_stat = self.materials.stat()
                    except FileNotFoundError:
                        pass  # An unused configured namespace has an explicit empty snapshot.
                    else:
                        if not stat.S_ISDIR(material_stat.st_mode):
                            raise PoiseError('Task material namespace must be a directory; correct the source and retry')
                        def fail_walk(error):
                            raise error
                        for directory, folders, files in os.walk(self.materials, onerror=fail_walk):
                            relative = Path(directory).relative_to(self.materials)
                            for folder in sorted(folders):
                                bundle.writestr(f'task-materials/{(relative / folder).as_posix()}/', b'')
                            for filename in sorted(files):
                                path = Path(directory) / filename
                                entry = 'task-materials/' + path.relative_to(self.materials).as_posix()
                                with path.open('rb') as source, bundle.open(entry, 'w') as target:
                                    shutil.copyfileobj(source, target)
                _sync_file(archive)
                size = archive.stat().st_size
                os.replace(archive, destination)
                published = True
                try:
                    _sync_directory(self.directory)
                except OSError as exc:
                    raise PoiseError(
                        f'Backup was published as {name}, but directory sync failed; '
                        f'keep writers stopped and verify it with backup restore before retrying: {exc}'
                    ) from exc
            return BackupCopy(name, live_size, size)
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
            if published:
                raise PoiseError(
                    f'Backup was published as {name}, but temporary cleanup failed; '
                    f'keep writers stopped and remove {pending} before retrying: {exc}'
                ) from exc
            raise PoiseError(f'Cannot create Task DB/material backup; correct the source and retry: {exc}') from exc

    @contextmanager
    def _unpacked_bundle(self, name):
        selected = self._selected(name)
        with tempfile.TemporaryDirectory(dir=self.database.parent, prefix='.backup-read-', suffix='.pending') as pending:
            stage = Path(pending)
            try:
                with zipfile.ZipFile(selected) as bundle:
                    names = bundle.namelist()
                    if len(names) != len(set(names)) or 'task.sqlite' not in names or 'task-materials/' not in names:
                        raise PoiseError('Incomplete Task backup ZIP; select a copy containing DB and materials')
                    for item in bundle.infolist():
                        if item.filename != 'task.sqlite' and not item.filename.startswith('task-materials/'):
                            raise PoiseError('Unexpected Task backup ZIP entry; select a complete native backup')
                        relative = item.filename.rstrip('/')
                        if '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
                            raise PoiseError('Invalid relative Task backup ZIP entry; select a complete native backup')
                        destination = descendant(stage, relative)
                        if item.is_dir():
                            destination.mkdir(parents=True, exist_ok=True)
                        else:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            with bundle.open(item) as source, destination.open('wb') as target:
                                shutil.copyfileobj(source, target)
                if not (stage / 'task-materials').is_dir():
                    raise PoiseError('Task backup materials entry must be a directory; select a complete copy')
                _integrity(stage / 'task.sqlite')
            except (OSError, sqlite3.Error, zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as exc:
                raise PoiseError(f'Cannot read Task backup ZIP {name}; select a complete readable copy: {exc}') from exc
            yield stage

    @contextmanager
    def database_copy(self, name):
        """Read a verified DB from the same snapshot format used by restore."""
        with self.locked():
            with self._database_copy_owned(name) as source:
                yield source

    @contextmanager
    def _database_copy_owned(self, name):
        """Read a verified source while the caller owns the backup resource."""
        if self.materials is None:
            selected = self._selected(name)
            _integrity(selected)
            yield selected
        else:
            with self._unpacked_bundle(name) as stage:
                yield stage / 'task.sqlite'

    def _install_bundle(self, stage):
        self.materials.parent.mkdir(parents=True, exist_ok=True)
        pending = Path(tempfile.mkdtemp(dir=self.materials.parent, prefix='.task-materials.restore-', suffix='.pending'))
        old_moved = new_installed = committed = retain_previous = False
        try:
            shutil.copytree(stage / 'task-materials', pending / 'new')
            for directory, _, files in os.walk(pending / 'new', topdown=False):
                for filename in files:
                    _sync_file(Path(directory) / filename)
                _sync_directory(Path(directory))
            with self._staged_database(stage / 'task.sqlite') as database:
                if self.materials.exists():
                    os.replace(self.materials, pending / 'previous')
                    old_moved = True
                os.replace(pending / 'new', self.materials)
                new_installed = True
                _sync_directory(self.materials.parent)
                os.replace(database, self.database)
                committed = True
                _sync_directory(self.database.parent)
        except OSError as exc:
            if committed:
                raise PoiseError(
                    f'Task DB and materials are installed, but sync/cleanup failed; keep writers stopped, '
                    f'check SQLite integrity and attachment reads, then remove {pending}: {exc}'
                ) from exc
            try:
                if new_installed:
                    shutil.rmtree(self.materials)
                if old_moved:
                    os.replace(pending / 'previous', self.materials)
            except OSError as rollback:
                retain_previous = True
                recovery = (
                    f'replace {self.materials} with the original namespace from {pending / "previous"}'
                    if old_moved else f'remove {self.materials} to restore its previous absence'
                )
                raise PoiseError(
                    f'Task DB was not replaced; materials rollback failed. Keep writers stopped; '
                    f'{recovery}, '
                    f'then retry backup restore. Install error: {exc}; rollback error: {rollback}'
                ) from rollback
            raise
        finally:
            if not retain_previous:
                try:
                    shutil.rmtree(pending)
                except OSError as exc:
                    state = 'installed' if committed else 'unchanged'
                    raise PoiseError(
                        f'Task DB/material targets are {state}, but temporary cleanup failed; '
                        f'keep writers stopped and remove {pending} before retrying: {exc}'
                    ) from exc

    def restore_copy(self, name: str) -> None:
        with self.locked():
            self._restore_copy(name)

    def _restore_copy(self, name: str) -> None:
        installed = False
        try:
            if self.materials is not None:
                with self._unpacked_bundle(name) as stage:
                    self._install_bundle(stage)
                    installed = True
            else:
                with self._database_copy_owned(name) as source, self._staged_database(source) as database:
                    os.replace(database, self.database)
                    try:
                        _sync_directory(self.database.parent)
                    except OSError as exc:
                        raise PoiseError(
                            f'Live Task DB was replaced from {name}, but directory sync failed; '
                            f'keep writers stopped and run SQLite PRAGMA integrity_check before retrying: {exc}'
                        ) from exc
        except (OSError, sqlite3.Error) as exc:
            if installed:
                raise PoiseError(
                    f'Task DB and materials are installed, but read-stage cleanup failed; '
                    f'keep writers stopped, check SQLite integrity/attachment reads and remove '
                    f'the reported temporary path: {exc}'
                ) from exc
            raise PoiseError(f'Cannot restore backup {name}; keep writers stopped, correct the error and retry: {exc}') from exc

    @contextmanager
    def locked(self):
        if not self.directory.exists():
            yield
            return
        try:
            with blocking_directory_lock(self.directory):
                yield
        except OSError as exc:
            raise PoiseError(f'Backup resource operation failed; correct the reported '
                             f'filesystem obstacle and retry the exact operation: {exc}') from exc

    def operator_identity(self):
        return f'uid:{os.getuid()}'

    def archive_exists(self, name):
        return (self.directory / name).is_file()

    def archive_digest(self, name):
        path = self._selected(name)
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        return digest.hexdigest()

    def delete_archive(self, name):
        path = self.directory / name
        if not path.exists():
            return False
        self._selected(name).unlink()
        _sync_directory(self.directory)
        return True

    def _grant_path(self, name, pending=False):
        return self.directory / (f'.{name}.retention.json.pending' if pending else f'{name}.retention.json')

    def grant_names(self):
        if not self.directory.exists():
            return ()
        return tuple(sorted(path.name[:-len('.retention.json')]
                            for path in self.directory.glob('*.retention.json')))

    def grant_state(self, name):
        final, pending = self._grant_path(name), self._grant_path(name, True)
        if pending.exists():
            return None, True
        if not final.exists():
            return None, False
        try:
            grant = json.loads(final.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise PoiseError(f'Cannot read retention metadata for {name}: {exc}') from exc
        if not isinstance(grant, dict):
            raise PoiseError(f'Retention metadata for {name} must be a JSON object; preserve '
                             'this archive and reconcile its exact recorded source before retrying')
        return grant, False

    def publish_grant(self, name, grant):
        final, pending = self._grant_path(name), self._grant_path(name, True)
        try:
            for path in (final, pending):
                if path.exists() and json.loads(path.read_text(encoding='utf-8')) != grant:
                    raise PoiseError(f'Retention request conflict for {name}; reconcile exact pending/active decision')
            if final.exists():
                _sync_directory(self.directory)
                return
            if not pending.exists():
                with pending.open('x', encoding='utf-8') as stream:
                    json.dump(grant, stream, sort_keys=True, ensure_ascii=False)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                _sync_directory(self.directory)
            os.replace(pending, final)
            _sync_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise PoiseError(f'Retention publication interrupted for {name}; preserve pending state and '
                             f'resume the same request: {exc}') from exc

    def delete_grant(self, name):
        self._grant_path(name).unlink(missing_ok=True)
        _sync_directory(self.directory)
