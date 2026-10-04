"""Linux no-follow observations and create-only same-directory publication."""
from __future__ import annotations
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import subprocess
import uuid

from ..modules.local_assets.domain import AssetSpec, LocalAssetError, PublicationFailure, RestoreRequest


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def root_fd(value: str, code: str) -> int:
    path = Path(value)
    if not path.is_absolute() or str(path) != value or any(part in (".", "..") for part in value.split("/")):
        raise LocalAssetError(code)
    fd = os.open("/", _DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError as exc:
        os.close(fd)
        raise LocalAssetError(code) from exc


class FileLocalAssets:
    @contextmanager
    def open_roots(self, request: RestoreRequest):
        target = root_fd(request.target_root, "invalid_target_root")
        source = None
        try:
            try:
                git = subprocess.run(["git", "-C", request.target_root, "rev-parse", "--show-toplevel"],
                                     capture_output=True, text=True, check=False)
            except OSError as exc:
                raise LocalAssetError("invalid_target_root") from exc
            if git.returncode or git.stdout.rstrip("\n") != request.target_root:
                raise LocalAssetError("invalid_target_root")
            source = root_fd(request.source_root, "unsafe_source_root")
            source_path, target_path = Path(request.source_root), Path(request.target_root)
            if (source_path == target_path or source_path in target_path.parents or target_path in source_path.parents
                    or identity(source) == identity(target)):
                raise LocalAssetError("unsafe_source_root")
            yield _Storage(target, source, request.target_root)
        finally:
            if source is not None:
                os.close(source)
            os.close(target)


class _Storage:
    def __init__(self, target: int, source: int, target_path: str):
        self.target, self.source = target, source
        self.target_path = target_path

    @contextmanager
    def parent(self, root: int, path: str, asset: str, invalid: str, create: bool = False):
        fd = os.dup(root)
        missing = False
        try:
            for part in path.split("/")[:-1]:
                try:
                    child = os.open(part, _DIRECTORY, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        missing = True
                        break
                    try:
                        os.mkdir(part, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child = self.open_directory(fd, part, asset, invalid)
                except OSError as exc:
                    self.classify(fd, part, asset, invalid, exc)
                    raise
                os.close(fd)
                fd = child
            yield None if missing else fd
        finally:
            os.close(fd)

    @staticmethod
    def classify(parent: int, name: str, asset: str, invalid: str, exc: OSError):
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            raise exc
        if stat.S_ISLNK(info.st_mode):
            raise LocalAssetError("unsafe_symlink", asset) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise LocalAssetError(invalid, asset) from exc
        raise exc

    def open_directory(self, parent: int, name: str, asset: str, invalid: str) -> int:
        try:
            return os.open(name, _DIRECTORY, dir_fd=parent)
        except OSError as exc:
            self.classify(parent, name, asset, invalid, exc)
            raise

    @staticmethod
    def observe(parent: int, name: str, asset: str, invalid: str):
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise LocalAssetError("unsafe_symlink", asset)
        if not stat.S_ISREG(info.st_mode):
            raise LocalAssetError(invalid, asset)
        return info

    def source_bytes(self, asset: AssetSpec) -> bytes:
        with self.parent(self.source, asset.source_path, asset.path, "invalid_source_asset") as parent:
            name = asset.source_path.split("/")[-1]
            if parent is None or self.observe(parent, name, asset.path, "invalid_source_asset") is None:
                raise asset.missing()
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError as exc:
                raise asset.missing() from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise LocalAssetError("unsafe_symlink", asset.path) from exc
                raise
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise LocalAssetError("invalid_source_asset", asset.path)
                return stream.read()

    def destination_exists(self, asset: AssetSpec) -> bool:
        with self.parent(self.target, asset.path, asset.path, "invalid_destination_asset") as parent:
            return parent is not None and self.observe(parent, asset.path.split("/")[-1], asset.path,
                                                       "invalid_destination_asset") is not None

    def revalidate(self, parent: int, asset: AssetSpec) -> None:
        self.revalidate_root()
        with self.parent(self.target, asset.path, asset.path, "invalid_destination_asset") as current:
            if current is None or identity(current) != identity(parent):
                raise LocalAssetError("invalid_destination_asset", asset.path)

    def publish_missing(self, asset: AssetSpec, content: bytes) -> str:
        outcome = None
        try:
            self.revalidate_root()
            with self.parent(self.target, asset.path, asset.path, "invalid_destination_asset", create=True) as parent:
                name = asset.path.split("/")[-1]
                self.revalidate(parent, asset)
                if self.observe(parent, name, asset.path, "invalid_destination_asset") is not None:
                    return "preserved"
                temporary = ".local-assets-" + uuid.uuid4().hex
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fchmod(stream.fileno(), asset.mode)
                        os.fsync(stream.fileno())
                    self.revalidate(parent, asset)
                    try:
                        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                        outcome = "created"
                    except FileExistsError:
                        if self.observe(parent, name, asset.path, "invalid_destination_asset") is None:
                            raise LocalAssetError("invalid_destination_asset", asset.path)
                        outcome = "preserved"
                    os.fsync(parent)
                finally:
                    os.unlink(temporary, dir_fd=parent)
                    os.fsync(parent)
                return outcome
        except OSError as exc:
            raise PublicationFailure(asset.path, outcome) from exc

    def revalidate_root(self) -> None:
        current = root_fd(self.target_path, "invalid_target_root")
        try:
            if identity(current) != identity(self.target):
                raise LocalAssetError("invalid_target_root")
        finally:
            os.close(current)
