"""Bounded ordinary UTF-8 source snapshots for the standalone reader."""
import os
from pathlib import Path
import stat

from domain.generic import Problem
from domain.reader import ReaderDocument, ReaderVersion, reader_invalid


class ReaderFiles:
    def cwd(self, value):
        try:
            path = Path(value)
            if not path.is_absolute() or not path.is_dir():
                reader_invalid('/input/cwd')
            return path.resolve()
        except OSError:
            reader_invalid('/input/cwd')

    def path(self, value, cwd=None):
        path = Path(value)
        path = path if path.is_absolute() else cwd / path
        try:
            return path.resolve()
        except OSError:
            self.unavailable(path.absolute())

    def unavailable(self, path):
        raise Problem('reader_source_unavailable', {'path': str(path)},
                      'configure:source', 'unavailable')

    def read(self, path, limits, expected_version=None):
        try:
            preliminary = path.stat()
            if not stat.S_ISREG(preliminary.st_mode) or preliminary.st_size > limits.max_file_bytes:
                self.unavailable(path)
            with path.open('rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > limits.max_file_bytes:
                    self.unavailable(path)
                version = ReaderVersion(before.st_size, before.st_mtime_ns)
                if expected_version is not None and version.view() != expected_version:
                    raise Problem('reader_version_changed', {'path': str(path),
                                  'expected': expected_version, 'observed': version.view()}, 'reader:read')
                raw = source.read(limits.max_file_bytes + 1)
                after = os.fstat(source.fileno())
                if version != ReaderVersion(after.st_size, after.st_mtime_ns):
                    raise Problem('reader_file_changed', {'path': str(path)}, 'reader:read')
            if len(raw) > limits.max_file_bytes:
                self.unavailable(path)
            text = raw.decode('utf-8')
            if '\0' in text:
                self.unavailable(path)
            return ReaderDocument(str(path), version, raw, text)
        except (OSError, UnicodeError):
            self.unavailable(path)
