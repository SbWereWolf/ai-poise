"""Shared binary, non-overwriting publication; callers own admission and locks."""
import os
import stat
import hashlib
import tempfile
from pathlib import Path
from ..common import PoiseError


class BinaryFilePublisher:
    @staticmethod
    def without_links(path, label):
        if not path.is_absolute():
            raise PoiseError(f'{label} must be absolute')
        cursor = Path(path.anchor)
        for part in path.parts[1:]:
            cursor /= part
            if cursor.is_symlink():
                raise PoiseError(f'{label} contains a symlink: {cursor}')
            if cursor.exists() and cursor != path and not cursor.is_dir():
                raise PoiseError(f'{label} parent is not a directory: {cursor}')

    @staticmethod
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def read(cls, path, label, output=None):
        """Read binary material without following links or blocking on a FIFO."""
        cls.without_links(path, label)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise PoiseError(f'{label} is not a regular file: {path}')
            digest = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                chunk = source.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise PoiseError(f'{label} changed while reading: {path}')
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
                remaining -= len(chunk)
            after = os.fstat(source.fileno())
            cls.without_links(path, label)
            if (cls.signature(before) != cls.signature(after)
                    or cls.signature(path.lstat()) != cls.signature(before)):
                raise PoiseError(f'{label} changed while reading: {path}')
        return digest.hexdigest()

    @classmethod
    def publish(cls, source, target, digest, mode):
        source, target = Path(source), Path(target)
        cls.without_links(target, 'Destination')
        if target.exists():
            if cls.read(target, 'Destination') != digest:
                raise PoiseError('Destination conflict')
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        cls.without_links(target, 'Destination')
        fd, temporary = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), mode)
                if cls.read(source, 'Source', output) != digest:
                    raise PoiseError('Source digest changed')
                output.flush()
                os.fsync(output.fileno())
            cls.without_links(target, 'Destination')
            try:
                os.link(temporary, target)
            except FileExistsError:
                if cls.read(target, 'Destination') != digest:
                    raise PoiseError('Destination conflict')
            cls.sync_parent(target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def sync_parent(path):
        fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
