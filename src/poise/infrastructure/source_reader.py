"""Bounded regular UTF-8 reads and atomic metadata receipts, separate from Task storage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from ..application.source_reader import SourceReader
from ..modules.source_reader.domain import ReadLimits
from ..common import PoiseError, encoded, digest
from .locking import exclusive_lock
from .goal_config import atomic_write, strict_json


class ReadReceipts:
    def __init__(self, path, limits):
        self.path, self.limits = Path(path), limits

    @contextmanager
    def transaction(self):
        if self.path.is_symlink():
            raise PoiseError('Reader state must not be a symlink')
        with exclusive_lock(self.path.with_suffix(self.path.suffix+'.lock'),
                            self.limits.lock_wait_seconds, self.limits.lock_poll_seconds):
            if self.path.exists():
                try:
                    with self.path.open('rb') as stream:
                        raw = stream.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('Reader receipt storage exceeds 16 MiB')
                    value = strict_json(raw.decode('utf-8'))
                    if (not isinstance(value, dict) or value.get('schema') != 'source-read-receipts-1'
                            or type(value.get('generation')) is not int or value['generation'] < 1
                            or not isinstance(value.get('receipts'), list)
                            or len(value['receipts']) > self.limits.max_receipts
                            or any(not isinstance(r, dict) or 'receipt_id' not in r
                                   or type(r.get('acknowledged')) is not bool
                                   or r.get('generation') != value['generation'] for r in value['receipts'])):
                        raise ValueError('Invalid reader state')
                except (ValueError, KeyError, UnicodeError) as exc:
                    raise PoiseError(f'Cannot read receipt state: {exc}') from exc
            else:
                value = {'schema': 'source-read-receipts-1', 'generation': 1,
                         'reason': 'startup', 'event_id': None, 'receipts': []}
            before = encoded(value)
            yield value
            after = encoded(value)
            if len(after.encode('utf-8')) > 16 * 1024 * 1024:
                raise PoiseError('Reader receipt storage exceeds 16 MiB')
            if before != after or not self.path.exists():
                atomic_write(self.path, (after+'\n').encode('utf-8'), 0o600)


class SourceFiles:
    @staticmethod
    def read(cwd, item, limits):
        base = Path(cwd).resolve(strict=True)
        if not base.is_dir():
            raise PoiseError('cwd must be a directory')
        path = Path(item['path'])
        try:
            path = (path if path.is_absolute() else base/path).resolve(strict=True)
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                initial = os.fstat(stream.fileno())
                if not stat.S_ISREG(initial.st_mode):
                    raise PoiseError('Source must be a regular file')
                data = stream.read(limits.max_file_bytes+1)
                final = os.fstat(stream.fileno())
            if (initial.st_ino, initial.st_size, initial.st_mtime_ns) != (final.st_ino, final.st_size, final.st_mtime_ns):
                raise PoiseError('Source changed while reading')
            if path.stat().st_ino != final.st_ino:
                raise PoiseError('Source replaced while reading')
            if len(data) > limits.max_file_bytes:
                raise PoiseError('Source exceeds configured file byte bound')
            if b'\0' in data:
                raise PoiseError('Binary source is not a UTF-8 document')
            lines = data.decode('utf-8').splitlines(keepends=True)
            if item['start'] > len(lines) or item['end'] > len(lines):
                raise PoiseError('Requested range lies beyond end of file')
            return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                    'start': item['start'], 'end': item['end'],
                    'text': ''.join(lines[item['start']-1:item['end']])}
        except (OSError, UnicodeError, ValueError) as exc:
            raise PoiseError(f'Cannot read explicit source {path}: {exc}') from exc

    @staticmethod
    def receipt(value, generation):
        metadata = {key: value[key] for key in ('path', 'sha256', 'start', 'end')}
        metadata['generation'] = generation
        return {**metadata, 'receipt_id': digest(metadata),
                'observed_at': datetime.now(timezone.utc).isoformat()}


class FileSourceReader(SourceReader):
    def __init__(self, state, policy):
        limits = ReadLimits.parse(policy)
        super().__init__(SourceFiles(), ReadReceipts(state, limits), limits)

    def observe_context(self):
        """Read current generation without constructing an empty receipt store."""
        from .diagnostic_io import json_file
        state = json_file(self.receipts.path, 16 * 1024 * 1024)
        if (not isinstance(state, dict) or state.get('schema') != 'source-read-receipts-1'
                or type(state.get('generation')) is not int or state['generation'] < 1
                or state.get('reason') not in ('startup', 'resume', 'clear', 'compact', 'manual')
                or (state.get('event_id') is not None and not isinstance(state['event_id'], str))
                or not isinstance(state.get('receipts'), list)
                or len(state['receipts']) > self.limits.max_receipts
                or any(not isinstance(r, dict) or type(r.get('acknowledged')) is not bool
                       or not isinstance(r.get('receipt_id'), str)
                       or r.get('generation') != state['generation'] for r in state['receipts'])):
            raise PoiseError('Invalid source reader observation')
        return self._context(state)
